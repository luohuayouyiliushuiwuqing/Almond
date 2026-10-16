#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scrapling 底层抓取功能实测脚本（纯控制台，不生成任何网页文件）。

输入指定网页 URL，逐层调用底层抓取函数并打印结果：

  1. Fetcher（静态，curl_cffi）        —— 直连，配了代理再测一次走代理
  2. DynamicFetcher（浏览器 JS 渲染）   —— 需要 chromium
  3. StealthyFetcher（反爬）           —— 需要 chromium
  4. almond.core.fetcher.get_metadata  —— Almond 生产链路（三级降级 + 元数据提取）端到端

用法：
  python tests/scrapling_test.py https://example.com https://your-site.com/page
  python tests/scrapling_test.py                  # 不带参数用默认示例站
  python tests/scrapling_test.py --proxy http://127.0.0.1:7897   # 指定代理（默认自动探测端口）
  python tests/scrapling_test.py --no-proxy       # 强制不探测代理（测纯直连）
  python tests/scrapling_test.py --no-browser     # 跳过浏览器引擎（更快）
  python tests/scrapling_test.py --full           # 忽略经验/策略，强制跑全部分层
  python tests/scrapling_test.py -v               # 输出 DEBUG 级日志（逐级抓取明细）
  python tests/scrapling_test.py https://a.com --cookie "sid=1; token=2"
                                                 # 为该域名存入 Cookie（浏览器里原样复制），
                                                 # 之后 fetch_page 每次都 Cookie 优先直取
  python tests/scrapling_test.py --timeout 10     # 单次抓取超时秒数

日志：统一走 almond.core.log（loguru），格式「时间 | 级别 | 文件:行号 | 消息」，
  终端按级别着色；测试报告的 PASS/FAIL/SKIP 也走同一出口（SUCCESS/INFO/ERROR 级）。

经验缓存（metadata.db 的 `fetch_hints` 表，原 cache/fetch_hints.json 已迁库）：
- 已知直连不通的域名不跑直连；代理转发不通时快速失败而不是烧满超时。
- **智能模式（默认）**：该域名已有可行策略时，分层用例全部跳过，只经 fetch_page
  做一次「策略直用」（学成一次，之后每次都是单请求）；想重新验证 scrapling 各层用 --full。
- 重置学习：清空 metadata.db 的 `fetch_hints` 表数据（或删除整个存储目录重建）。

退出码：有失败用例 = 1，全部通过 = 0。
"""
from __future__ import annotations

import argparse
import platform
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# 脚本住在 tests/ 下：把仓库根加进 sys.path，保证能 import almond.*
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from almond.core import fetch_hints, hints_cookie, hints_strategy
    from almond.core.proxy import proxy_reachable
except Exception:  # noqa: BLE001 —— 不在仓库里跑时退化为无经验缓存的裸测试
    fetch_hints = hints_cookie = hints_strategy = None
    proxy_reachable = None

try:
    from almond.core.log import logger, set_level
except Exception:  # noqa: BLE001 —— 没装 loguru 时退回朴素 print，接口对齐 loguru

    class _FallbackLogger:
        def __getattr__(self, _name):
            return lambda msg, *args, **kwargs: print(msg)

    logger = _FallbackLogger()
    set_level = lambda _level: None  # noqa: E731

DEFAULT_TARGETS = ["https://example.com", "https://www.baidu.com"]
PROXY_PORTS = range(7889, 7900)
PROBE_TIMEOUT = 0.25

PASS, FAIL, SKIP = "pass", "fail", "skip"
_counts = {PASS: 0, FAIL: 0, SKIP: 0}


class Skip(Exception):
    """用例跳过（条件不满足），不算失败。"""


def run_case(name: str, fn) -> None:
    """执行一个用例并打印一行结果；fn 返回详情字符串（可选）。"""
    t0 = time.time()
    status, detail, error = PASS, "", ""
    try:
        detail = fn() or ""
    except Skip as exc:
        status, detail = SKIP, str(exc)
    except Exception as exc:  # noqa: BLE001 —— 测试脚本：任何异常都算失败
        status, error = FAIL, f"{type(exc).__name__}: {exc}"
    ms = round((time.time() - t0) * 1000)
    _counts[status] += 1

    mark = {PASS: "PASS", FAIL: "FAIL", SKIP: "SKIP"}[status]
    line = f"[{mark}] {name}  ({ms}ms)"
    if detail:
        line += f"  · {detail}"
    if error:
        line += f"\n         ↳ {error}"
    # 报告也走统一日志出口：PASS→SUCCESS(绿) / FAIL→ERROR(红) / SKIP→INFO
    if status == PASS:
        logger.success(line)
    elif status == FAIL:
        logger.error(line)
    else:
        logger.info(line)


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _make_skip(reason: str):
    def fn():
        raise Skip(reason)

    return fn


# ---------------------------------------------------------------- 环境与代理

def _chromium_path() -> str | None:
    import os

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            path = p.chromium.executable_path
        return path if path and os.path.exists(path) else None
    except Exception:  # noqa: BLE001
        return None


def _has_almond() -> bool:
    # 脚本在 tests/ 下，仓库根是上一级
    return (Path(__file__).resolve().parent.parent / "almond").exists()


def check_env() -> str | None:
    """打印环境一行，返回 chromium 路径（None = 浏览器引擎不可用）。"""
    import scrapling

    chromium = _chromium_path()
    logger.info(f"环境: Python {platform.python_version()} · scrapling "
                f"{getattr(scrapling, '__version__', '?')} · "
                f"chromium {'就位' if chromium else '缺失（浏览器引擎将跳过）'}")
    if chromium:
        logger.info(f"chromium: {chromium}")
    return chromium


def probe_proxy_ports() -> str | None:
    """TCP 探测 7889-7899，返回第一个可连端口的代理地址或 None。"""

    def probe(port: int) -> int | None:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=PROBE_TIMEOUT):
                return port
        except OSError:
            return None

    with ThreadPoolExecutor(max_workers=len(PROXY_PORTS)) as pool:
        for port in pool.map(probe, PROXY_PORTS):
            if port is not None:
                return f"http://127.0.0.1:{port}"
    return None


# ---------------------------------------------------------------- 各层抓取

def _title_of(response) -> str:
    try:
        els = response.css("title")
        if els:
            return (els[0].text or "").strip()[:80]
    except Exception:  # noqa: BLE001
        pass
    return ""


def _resp_detail(resp) -> str:
    status = getattr(resp, "status", None)
    html = resp.html_content or ""
    assert_true(status is not None and status < 400, f"HTTP {status}")
    assert_true(len(html) > 0, "响应体为空")
    return f"HTTP {status} · {len(html)} B · title={_title_of(resp) or '(无)'}"


def test_url(url: str, timeout: int, proxy: str | None,
             chromium: str | None, with_browser: bool, smart: bool = True) -> None:
    logger.info(f"=== {url} ===")

    from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher

    # 经验缓存整 URL 拦截：已知直连不通且（没代理 / 代理不通）→ 全部用例秒跳过
    blocked = fetch_hints.skip_reason(url, proxy) if fetch_hints else None
    if blocked:
        logger.warning(f"⚠ {blocked}")
        for name in (f"Fetcher 直连 {url}", f"Fetcher 走代理 {url}",
                     f"DynamicFetcher(JS渲染) {url}", f"StealthyFetcher(反爬) {url}",
                     f"almond get_metadata 端到端 {url}"):
            run_case(name, _make_skip(blocked))
        return

    # 智能模式：已有可行策略 **或 Cookie** → 分层用例不再重跑，只经 fetch_page 做单发直取。
    # 分层是裸调 scrapling 的（不吃策略/经验短路），全量跑只在 --full 或还没学出东西时做。
    strat = hints_strategy.get_strategy(url) if fetch_hints else None
    cookie = hints_cookie.get_cookie(url) if fetch_hints else None
    if smart and (strat or cookie):
        if strat and cookie:
            note = (f"已有 Cookie + 策略 {strat['id']}（最快 {strat.get('best_ms')}ms），"
                    f"智能模式只做单发直取（--full 强制全层）")
        elif cookie:
            note = "已有 Cookie，智能模式只做 Cookie 直取（--full 强制全层）"
        else:
            note = (f"已有策略 {strat['id']}（最快 {strat['best_ms']}ms），智能模式只做策略直用"
                    f"（--full 强制全层）" if strat.get("best_ms") is not None
                    else f"已有策略 {strat['id']}，智能模式只做策略直用（--full 强制全层）")
        logger.info(f"⚡ {note}")
        for name in (f"Fetcher 直连 {url}", f"Fetcher 走代理 {url}",
                     f"DynamicFetcher(JS渲染) {url}", f"StealthyFetcher(反爬) {url}"):
            run_case(name, _make_skip(note))
        _almond_case(url, timeout, proxy)
        return

    # 代理端口在 ≠ 能用：先做转发健康检查，不通就别把每层都拖到超时
    usable = proxy
    if proxy and fetch_hints and not proxy_reachable(proxy):
        usable = None
        logger.warning(f"⚠ 代理 {proxy} 转发不通（健康检查失败）：走代理用例快速失败，其余降级直连")

    # 1) 静态 Fetcher：直连（已知直连不通就不跑）
    def direct():
        if fetch_hints and fetch_hints.proxy_needed(url):
            raise Skip("经验缓存：已知直连不通，跳过直连，直接走代理")
        try:
            resp = Fetcher.get(url, timeout=timeout, proxy=None)
        except Exception as exc:  # noqa: BLE001
            if fetch_hints and fetch_hints.is_network_error(str(exc)):
                fetch_hints.record_direct_failure(url)   # 学到了：这个域名直连不通
            raise
        if fetch_hints:
            fetch_hints.record_direct_success(url)
        return _resp_detail(resp)

    run_case(f"Fetcher 直连 {url}", direct)

    # 1b) 静态 Fetcher：走代理（代理不通 = 快速失败，不跑抓取）
    def via_proxy():
        if not proxy:
            raise Skip("没有可用代理（--proxy 或端口探测均为空）")
        if fetch_hints and not proxy_reachable(proxy):
            raise AssertionError(
                f"代理 {proxy} 端口在但转发不通（健康检查失败）→ 不跑抓取，待恢复后重试")
        resp = Fetcher.get(url, timeout=timeout, proxy=proxy)
        if fetch_hints:
            fetch_hints.record_proxy_success(url)
        return _resp_detail(resp)

    run_case(f"Fetcher 走代理 {url}", via_proxy)

    # 2/3) 浏览器引擎（usable=None 表示本 URL 降级直连）
    def browser_case(engine):
        def fn():
            if not with_browser:
                raise Skip("--no-browser 已跳过")
            if not chromium:
                raise Skip("chromium 未安装（playwright install chromium）")
            # 浏览器引擎 timeout 单位是毫秒
            try:
                resp = engine(url, timeout=timeout * 1000, proxy=usable)
            except Exception as exc:  # noqa: BLE001
                if fetch_hints and usable is None and fetch_hints.is_network_error(str(exc)):
                    fetch_hints.record_direct_failure(url)
                raise
            if fetch_hints:
                if usable:
                    fetch_hints.record_proxy_success(url)
                else:
                    fetch_hints.record_direct_success(url)
            return _resp_detail(resp)

        return fn

    run_case(f"DynamicFetcher(JS渲染) {url}", browser_case(DynamicFetcher.fetch))
    run_case(f"StealthyFetcher(反爬) {url}", browser_case(StealthyFetcher.fetch))

    _almond_case(url, timeout, proxy)


def _almond_case(url: str, timeout: int, proxy: str | None) -> None:
    """Almond 生产链路用例（get_metadata → fetch_page：吃策略/经验短路）。"""
    def almond_meta():
        if not _has_almond():
            raise Skip("当前目录没有 almond/ 包（请在仓库根目录运行）")
        from almond.core.fetcher import get_metadata

        meta = get_metadata(url, timeout=timeout, proxy=proxy)
        if not meta.get("success"):
            # fetch_page 内部可能又判了一次跳过（经验在本 URL 跑的过程中刚学到）
            reason = fetch_hints.skip_reason(url, proxy) if fetch_hints else None
            raise AssertionError(reason or "get_metadata success=False（三级降级链全部失败）")
        assert_true(bool(meta.get("title")), "提取不到标题 title=''")
        strat = hints_strategy.get_strategy(url) if fetch_hints else None
        sid = f"{strat['id']}（最快 {strat['best_ms']}ms）" if strat and strat.get("best_ms") is not None \
            else (strat["id"] if strat else "无")
        ck = hints_cookie.get_cookie(url) if fetch_hints else None
        ck_note = (f" · Cookie=有（最快 {ck['best_ms']}ms）" if ck and ck.get("best_ms") is not None
                   else (" · Cookie=有" if ck else ""))
        return (f"title={meta['title'][:60]} · thumbnail={'有' if meta['thumbnail'] else '无'}"
                f" · favicon={'有' if meta['favicon'] else '无'} · details={len(meta['details'])} 条"
                f" · 策略={sid}{ck_note}")

    run_case(f"almond get_metadata 端到端 {url}", almond_meta)


# ---------------------------------------------------------------- 主流程

def main() -> int:
    parser = argparse.ArgumentParser(description="Scrapling 底层抓取实测")
    parser.add_argument("urls", nargs="*", help="要抓取的网页 URL（默认两个示例站）")
    parser.add_argument("--proxy", default=None, help="代理地址，如 http://127.0.0.1:7897（默认自动探测）")
    parser.add_argument("--no-proxy", action="store_true", help="不指定也不自动探测代理（纯直连测试）")
    parser.add_argument("--timeout", type=int, default=15, help="单次抓取超时秒数（默认 15）")
    parser.add_argument("--no-browser", action="store_true", help="跳过浏览器引擎（更快）")
    parser.add_argument("--full", action="store_true",
                        help="忽略经验/策略，强制跑全部分层（默认：有策略时只做策略直用）")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="输出 DEBUG 级日志（逐级抓取尝试明细、HTTP 访问日志）")
    parser.add_argument("--cookie", default=None,
                        help="为唯一目标 URL 写入 Cookie（浏览器原样复制的 a=1; b=2），本次即 Cookie 优先")
    args = parser.parse_args()

    if args.verbose:
        set_level("DEBUG")

    targets = [u if "://" in u else "https://" + u for u in (args.urls or DEFAULT_TARGETS)]

    # Cookie 要落到具体域名 → 只允许配单个 URL
    if args.cookie:
        if len(targets) != 1:
            logger.error("--cookie 只能配合恰好 1 个 URL 使用（Cookie 是按域名存的）")
            return 2
        if not fetch_hints:
            logger.error("经验缓存不可用（almond 未导入），无法写入 Cookie")
            return 2
        hints_cookie.set_cookie(targets[0], args.cookie)
        logger.info(f"已写入 Cookie：{targets[0]}（{len(args.cookie)} 字符）→ 本次优先 Cookie 直取")
    logger.info(f"目标: {targets}")
    logger.info(f"超时: {args.timeout}s · 浏览器引擎: {'跳过' if args.no_browser else '启用'}"
                f" · 模式: {'--full 全层' if args.full else '智能（已有策略只做策略直用）'}")
    if fetch_hints:
        logger.info(f"经验缓存: {fetch_hints.path()}")

    chromium = check_env()

    if args.no_proxy:
        proxy = None
        logger.info("代理: --no-proxy 已指定，不做探测")
    else:
        proxy = args.proxy or probe_proxy_ports()
        logger.info(f"代理: {proxy or '未检测到（7889-7899 无监听，且未指定 --proxy）'}")
    logger.info("-" * 72)

    for url in targets:
        test_url(url, args.timeout, proxy, chromium,
                 with_browser=not args.no_browser, smart=not args.full)

    logger.info("-" * 72)
    logger.info(f"合计 通过 {_counts[PASS]} · 失败 {_counts[FAIL]} · 跳过 {_counts[SKIP]}")
    return 1 if _counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
