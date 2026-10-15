#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cookie 抓取与复用实测脚本（纯控制台，单一功能）。

一个测试只做一件事：**先把网页的 Cookie 抓下来存好，再用这个 Cookie 去抓网页数据**，
验证 Cookie 层的两段链路真的通：

  ① 抓 Cookie —— 无 Cookie 抓取目标页，把响应里的 Set-Cookie 收进经验缓存
     （fetch_hints，按域名存；fetch_page 成功即自动回传，这里显式再收一次保证幂等）；
     或者用 --cookie-json **从别处拿一个 Cookie 导入**（JSON，见下），直接进入 ②；
  ② 用 Cookie —— 再抓同一页面：fetch_page 第 0 步会带已存 Cookie 静态直取，
     校验数据拿到 **且** 确实走的是 Cookie 直取路径（last_success/last_ms 刷新 +
     日志双重判定）——对外部 Cookie 来说，这一步就是「能不能进入」的判定。

用法：
  python tests/cookie_test.py https://example.com/login-required
  python tests/cookie_test.py https://your-site.com/page --proxy http://127.0.0.1:7897
  python tests/cookie_test.py https://your-site.com/page --fresh   # 先清掉存量 Cookie 再重抓
  python tests/cookie_test.py https://your-site.com/page --show-cookie   # 打印 Cookie 完整值
  python tests/cookie_test.py https://your-site.com/page --timeout 10

  # 外部 Cookie（JSON）→ 导入 → 试能不能进入：
  python tests/cookie_test.py https://site.com/page --cookie-json '{"sid":"abc","token":"xyz"}'
  python tests/cookie_test.py https://site.com/page --cookie-json cookies.json   # 从文件读

--cookie-json 接受的 JSON 格式（二选一）：
  1. 扁平对象   {"sid": "abc", "token": "xyz"}
  2. 数组导出   [{"name": "sid", "value": "abc", "domain": ".site.com"}, …]
     （浏览器扩展 EditThisCookie 一类的导出；带 domain 的条目会按目标域名过滤，
       明显属于其它站的会被丢弃并计数；内联字符串或 .json 文件路径都行，文件按 UTF-8 读）

说明：
- Cookie 存在存储目录的 cache/fetch_hints.json（按根域名），脚本会打印文件位置。
- 页面不通过 Set-Cookie 下发 Cookie（只在 JS 里写）时 ① 会失败——那种 Cookie 请用
  `tests/scrapling_test.py <url> --cookie "a=1; b=2"` 从浏览器原样粘贴（分号串人工通道），
  或者用本脚本的 --cookie-json 传 JSON。
- 本脚本只认**恰好一个 URL**（单独的测试、单独的功能）；Cookie 的 7 天失效防抖、
  策略/经验等其余逻辑不在本脚本职责内。

日志：统一走 almond.core.log（loguru）；判定「Cookie 直取成功」的依据是
  fetch_hints 的 last_success/last_ms 刷新（touch_cookie 只在 Cookie 直取成功时写）
  加上捕获到的日志行，两者都报出来；被拒时还会从日志里抠出服务端的 HTTP 状态码。

退出码：① 或 ② 任一失败 = 1，两段全部通过 = 0。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

# 脚本住在 tests/ 下：把仓库根加进 sys.path，保证能 import almond.*
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from almond.core import fetch_hints, hints_cookie, hostutil  # noqa: E402
from almond.core.fetcher import fetch_page, get_metadata  # noqa: E402
from almond.core.log import logger  # noqa: E402


def _parse_cookie_json(raw: str, root: str) -> tuple[dict, int]:
    """解析外部 Cookie（JSON）→ ({键: 值}, 因域名不符被丢弃的条数)。

    输入是内联 JSON 文本（以 { 或 [ 开头）或一个 .json 文件路径。
    接受两种结构：扁平 {"k":"v"}；数组 [{"name":…, "value":…, "domain"?}:…}（浏览器扩展导出）。
    数组条目带 domain 时按目标根域名过滤——明显属于其它站的丢弃（计数返回给调用方打印），
    一个都对不上则报错（避免把整站导出盲目灌进这个域名的 Cookie 罐）。
    """
    text = raw.strip()
    if text.startswith(("{", "[")):
        payload = text
    else:
        p = Path(text).expanduser()
        if not p.is_file():
            raise ValueError(f"不是 JSON 文本，也不是存在的文件：{text}")
        payload = p.read_text(encoding="utf-8-sig")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        hint = ""
        if not text.startswith(("{", "[")):
            pass   # 文件内容坏了，就是坏
        elif '"' not in text:
            hint = "（引号疑似被 shell 吞掉——PowerShell 5.1 传内联 JSON 会这样，改用 .json 文件路径）"
        raise ValueError(f"JSON 解析失败：{exc}{hint}") from exc

    out: dict = {}
    foreign = 0
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)) or v is None:
                continue                      # 只收标量，嵌套结构不是 Cookie 键值
            out[str(k)] = str(v)
    elif isinstance(data, list):
        for item in data:
            if not (isinstance(item, dict) and "name" in item):
                continue
            d = str(item.get("domain") or "").lstrip(".").lower()
            if d and not (d == root or d.endswith("." + root) or root.endswith("." + d)):
                foreign += 1                  # 别的域名的 Cookie，不进本罐
                continue
            out[str(item["name"])] = str(item.get("value", ""))
        if not out and foreign:
            raise ValueError(
                f"JSON 里的 Cookie 全部属于其它域名（共 {foreign} 条），"
                f"与目标根域名 {root} 对不上"
            )
    else:
        raise ValueError("不支持的 JSON 结构：顶层要是对象 {…} 或数组 […]")
    if not out:
        raise ValueError("没解析出任何可用的键值对")
    return out, foreign


def _cookie_names(ck: dict | None) -> list[str]:
    return sorted(hints_cookie.parse_cookie_header((ck or {}).get("value", "")).keys())


def _show(ck: dict | None, show_value: bool) -> str:
    """Cookie 一行摘要：键名 + 值长度；--show-cookie 时带完整值。"""
    if not ck:
        return "（无）"
    names = _cookie_names(ck)
    keys = f"{len(names)} 个键：{', '.join(names) if names else '(空)'}"
    if not show_value:
        return keys
    return f"{keys}；值={ck.get('value', '')}"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="抓取网页 Cookie 并用该 Cookie 抓取数据（单一功能测试）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("url", help="目标网页（恰好一个 URL）")
    ap.add_argument("--proxy", default=None, help="代理地址，如 http://127.0.0.1:7897（默认直连）")
    ap.add_argument("--timeout", type=int, default=20, help="单次抓取超时秒数（默认 20）")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--fresh", action="store_true",
                     help="先清掉该域名的存量 Cookie 再抓（重新走 ① 抓取链路）")
    src.add_argument("--cookie-json", default=None, metavar="JSON|FILE",
                     help="外部 Cookie（JSON）：内联 '{\"sid\":\"abc\"}'，或编辑器导出的 "
                          "'[{\"name\":\"sid\",\"value\":\"abc\"}]'，或 .json 文件路径——"
                          "① 直接导入，② 用它试能不能进入")
    ap.add_argument("--show-cookie", action="store_true", help="打印 Cookie 完整值（默认只列键名）")
    args = ap.parse_args()

    url = args.url.strip()
    if not url.startswith(("http://", "https://")):
        print("FAIL 参数：URL 必须以 http:// 或 https:// 开头")
        return 1
    proxy = (args.proxy or "").strip() or None
    root = hostutil.root_domain(url) or url

    print(f"目标：{url}")
    print(f"代理：{proxy or '（直连）'}")
    print(f"Cookie 存储：{fetch_hints.path()}")

    ok1 = ok2 = False
    imported = bool(args.cookie_json)

    # ── ① 拿到 Cookie：导入外部 JSON，或抓页面收 Set-Cookie ────────
    if imported:
        print(f"① 导入外部 Cookie（JSON）→ 域名 {root} …")
        try:
            kv, foreign = _parse_cookie_json(args.cookie_json, root)
        except ValueError as exc:
            print(f"FAIL ① {exc}")
            return 1
        header = "; ".join(f"{k}={v}" for k, v in kv.items())
        hints_cookie.set_cookie(url, header)   # 人工写入 = 覆盖旧值 + 解除失效防抖
        after = hints_cookie.get_cookie(url)
        ok1 = True
        print(f"PASS ① 已导入 {len(kv)} 个键：{', '.join(kv)}"
              + (f"（丢弃其它域名 {foreign} 条）" if foreign else "")
              + (f"；值={header}" if args.show_cookie else ""))
    else:
        if args.fresh:
            # 人工写入空值 = 清空 Cookie 且顺带解除失效防抖（set_cookie 的既定语义），
            # 让 ① 重新走一遍纯净的「抓 Set-Cookie」链路
            hints_cookie.set_cookie(url, "")
            print("① 已按 --fresh 清空存量 Cookie，重新抓取…")
        before = hints_cookie.get_cookie(url)
        if before:
            print(f"① 存量 Cookie：{_show(before, args.show_cookie)}（本次响应若有轮换会合并刷新）")
        print(f"① 抓取 Set-Cookie：{url} …")
        t0 = time.time()
        resp = fetch_page(url, timeout=args.timeout, proxy=proxy)
        ms1 = round((time.time() - t0) * 1000)
        if resp is None:
            print(f"FAIL ① 首次抓取失败（{ms1}ms）——拿不到页面也就无从收 Cookie；"
                  f"若该站需要人工注入 Cookie，请用 --cookie-json 或 scrapling_test.py --cookie")
            return 1
        # fetch_page 成功时已自动收过（harvest-on-any-success）；显式再收一次幂等，
        # 同时让「① 在收 Cookie」这件事在脚本里看得见
        got_new = hints_cookie.store_cookie_from_response(url, resp)
        after = hints_cookie.get_cookie(url)
        if after:
            new_keys = set(_cookie_names(after)) - set(_cookie_names(before))
            ok1 = True
            print(f"PASS ① 收到 Cookie（{ms1}ms）：{_show(after, args.show_cookie)}"
                  + (f"；新增键 {sorted(new_keys)}" if new_keys else "")
                  + ("；本次响应无 Set-Cookie，沿用存量" if not got_new and before else ""))
        else:
            print(f"FAIL ① 页面未下发任何 Set-Cookie（{ms1}ms）——该站可能只在 JS 里写 Cookie；"
                  f"请改用 --cookie-json 传 JSON，或 scrapling_test.py --cookie 粘贴分号串")
            return 1

    # ── ② 用 Cookie 抓数据（外部 Cookie 模式下这一步就是「能不能进入」） ──
    print(f"② {'用外部 Cookie 试进入' if imported else '用存储的 Cookie 抓取数据'}：{url} …")
    ck_before = after
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="INFO")  # 捕获「Cookie 直取成功」
    t1 = time.time()
    try:
        meta = get_metadata(url, timeout=args.timeout, proxy=proxy)
    finally:
        logger.remove(sink)
    ms2 = round((time.time() - t1) * 1000)
    ck_after = hints_cookie.get_cookie(url)

    # 判定「确实走了 Cookie 直取」：
    #   last_success 只有 touch_cookie（Cookie 直取成功）会刷新——但它是秒级精度，
    #   同秒内两次直取会相等；last_ms 每次直取都写实测毫秒，一并参与判定；
    #   日志行「Cookie 直取成功」是旁证，一并打印
    last_before = (ck_before or {}).get("last_success", "")
    last_after = (ck_after or {}).get("last_success", "")
    ms_before = (ck_before or {}).get("last_ms")
    ms_after = (ck_after or {}).get("last_ms")
    stamp_changed = bool(last_after) and last_after != last_before
    ms_changed = ms_after is not None and ms_after != ms_before
    cookie_used = stamp_changed or ms_changed
    log_hit = any("Cookie 直取成功" in ln for ln in lines)
    cookie_dropped = ck_before is not None and ck_after is None
    # 被拒时从日志里抠出服务端返回的状态码（「Cookie 已失效（HTTP 403），弃用」）
    status = None
    for ln in lines:
        m = re.search(r"Cookie 已失效（HTTP\s*(\d+)）", ln)
        if m:
            status = m.group(1)
            break
    http_note = f"HTTP {status}" if status else "HTTP ≥400"

    if not meta.get("success"):
        if cookie_dropped:
            print(f"FAIL ② 不能进入（{ms2}ms）：服务端拒绝该 Cookie（{http_note}），已弃用；"
                  f"回退常规链也未拿到数据")
        else:
            print(f"FAIL ② 数据抓取失败（{ms2}ms）：三级抓取全部失败")
        return 1
    if cookie_used or log_hit:
        ok2 = True
        head = "能进入：外部 Cookie 直取成功" if imported else "Cookie 直取拿到数据"
        print(f"PASS ② {head}（{ms2}ms）：标题「{meta.get('title') or '(无标题)'}」"
              f"；last_success {last_before or '∅'} → {last_after}"
              + ("；日志命中「Cookie 直取成功」" if log_hit else ""))
    elif cookie_dropped:
        print(f"FAIL ② 不能进入（{ms2}ms）：服务端拒绝该 Cookie（{http_note}）已弃用；"
              f"虽回退常规链拿到了数据（标题「{meta.get('title') or '(无标题)'}」），"
              f"但 Cookie 本身**进不去**")
        return 1
    else:
        print(f"FAIL ② 数据拿到了（{ms2}ms，标题「{meta.get('title') or '(无标题)'}」），"
              f"但**没有**走 Cookie 直取路径（last_success/last_ms 未刷新）——回退了常规链")
        return 1

    print(f"结论：① {'导入外部' if imported else '抓'} Cookie {'PASS' if ok1 else 'FAIL'}"
          f" · ② {'试进入' if imported else '用 Cookie 抓数据'} {'PASS' if ok2 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
