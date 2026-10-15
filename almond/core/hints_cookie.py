"""Cookie 层：有 Cookie 先让 Cookie 走一趟，没有再走原逻辑。

按根域名存 `hosts.<domain>.cookie = {value, added, last_success, best_ms}`——
**该值不进任何 API**（`all_hints` 刻意排除）。来源两条：
① 人工 `set_cookie`（测试脚本 --cookie / --cookie-json）；
② **任意抓取成功**的 harvest-on-success（`store_cookie_from_response` 合并响应里的
   Set-Cookie，不额外发请求）。

防抖：`drop_cookie`（HTTP ≥400 判失效）记 `cookie_failed_at`，`COOKIE_BAN_DAYS` 天内
禁止自动回传——否则「静态成功存入 → 带 Cookie 403 弃用 → 又存入」会循环白跑。
人工 set_cookie 不受防抖限制。

存储原语全部来自 fetch_hints（效应通道与纪元守卫在那里，本模块只写数据）：
`LOCK` / `load()` / `save()` / `write_allowed()` / `record_effect()` / `host_of()` / `TS_FMT`。
"""

from datetime import datetime, timedelta

from almond.core.fetch_hints import (
    LOCK, TS_FMT, host_of, load, record_effect, save, write_allowed,
)

COOKIE_BAN_DAYS = 7          # Cookie 被判定失效后，N 天内禁止**自动**回传（防「收了又废」抖动）


def parse_cookie_header(value: str) -> dict:
    """把浏览器复制的 `a=1; b=2` 解析成 dict（值允许含 `=`）。解析不出返回 {}。"""
    out: dict = {}
    for seg in (value or "").split(";"):
        k, eq, v = seg.strip().partition("=")
        if eq and k:
            out[k] = v
    return out


def _header_of(mapping: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in mapping.items())


def _resp_cookies(response) -> dict:
    """从 scrapling Response 提取 {名: 值}（兼容 dict / get_dict() / Morsel 迭代）。"""
    c = getattr(response, "cookies", None)
    if not c:
        return {}
    try:
        if hasattr(c, "get_dict"):
            c = c.get_dict()
        if isinstance(c, dict):
            return {str(k): str(v) for k, v in c.items()}
        return {m.key: m.value for m in c}   # CookieJar → Morsel
    except Exception:  # noqa: BLE001
        return {}


def set_cookie(url: str, value: str) -> None:
    """人工写入 Cookie（原样粘贴浏览器里的 `a=1; b=2`），覆盖旧值。

    人工写入 = 明确意图，顺带**解除**失效防抖（`cookie_failed_at`）。
    """
    if not write_allowed():
        return
    domain = host_of(url)
    value = (value or "").strip()
    with LOCK:
        row = load()["hosts"].setdefault(domain, {})
        now = datetime.now().strftime(TS_FMT)
        row["cookie"] = {"value": value, "added": now}
        row.pop("cookie_failed_at", None)
        row["updated"] = now
        save(domain)


def get_cookie(url: str) -> dict | None:
    """读该域名的 Cookie：{value, added?, last_success?, best_ms?}；没有则 None。"""
    with LOCK:
        ck = load()["hosts"].get(host_of(url), {}).get("cookie")
        if isinstance(ck, dict) and ck.get("value"):
            return dict(ck)
    return None


def touch_cookie(url: str, ms: int | None = None, response=None, *, new_cookies: dict | None = None) -> None:
    """Cookie 直取成功：刷新 last_success、记耗时（best_ms 取最小）；
    响应里若带了新的 Set-Cookie（会话轮换），合并进已存的值里。

    `new_cookies`：效应重放入口——子进程把响应里的 Cookie 解析成 dict 带回
    （scrapling Response 不可序列化），主进程落地时用它代替 response。
    """
    if not write_allowed():
        return
    new = dict(new_cookies) if new_cookies is not None else _resp_cookies(response)
    if record_effect("touch_cookie", {"url": url, "ms": ms, "cookies": new}):
        return
    domain = host_of(url)
    with LOCK:
        ck = load()["hosts"].get(domain, {}).get("cookie")
        if not isinstance(ck, dict) or not ck.get("value"):
            return
        now = datetime.now().strftime(TS_FMT)
        ck["last_success"] = now
        if ms is not None:
            ck["last_ms"] = ms
            if ck.get("best_ms") is None or ms < ck["best_ms"]:
                ck["best_ms"] = ms
        if new:
            merged = parse_cookie_header(ck.get("value", ""))
            merged.update(new)
            ck["value"] = _header_of(merged)
        row = load()["hosts"][domain]
        row["updated"] = now
        save(domain)


def drop_cookie(url: str) -> None:
    """Cookie 失效（HTTP ≥400）→ 弃用，并记 `cookie_failed_at` 进入 7 天防抖期
    （期间自动回传被禁止；人工 set_cookie 或防抖到期后恢复）。"""
    if not write_allowed():
        return
    if record_effect("drop_cookie", {"url": url}):
        return
    domain = host_of(url)
    with LOCK:
        row = load()["hosts"].get(domain)
        if not row:
            return
        had = row.pop("cookie", None) is not None
        now = datetime.now().strftime(TS_FMT)
        if had:
            row["cookie_failed_at"] = now
        row["updated"] = now
        save(domain)


def _cookie_banned(row: dict) -> bool:
    """失效防抖是否仍在生效（**纯谓词**，不改数据；时间缺失按生效处理）。

    到期标记的清理在 `_store_cookie` 落地时顺手做——读路径不写。"""
    raw = row.get("cookie_failed_at")
    if not raw:
        return False
    try:
        failed = datetime.strptime(raw, TS_FMT)
    except (ValueError, TypeError):
        return True
    return datetime.now() - failed <= timedelta(days=COOKIE_BAN_DAYS)


def store_cookie_from_response(url: str, response) -> bool:
    """**任意引擎成功**后顺手回传 Cookie：把响应里的 Set-Cookie **合并**进缓存（没有则新建）。

    防抖：该域名的 Cookie 若在 COOKIE_BAN_DAYS 内被判定过失效（`cookie_failed_at`），
    自动回传被禁止——否则「静态成功存入 → 下次带 Cookie 403 → 弃用 → 又存入」会循环
    白跑。人工 set_cookie 不受此限。
    """
    new = _resp_cookies(response)
    if not new:
        return False
    return store_cookie(url, new)


def store_cookie(url: str, new: dict) -> bool:
    """把一组已解析的 Set-Cookie 合并进缓存（`store_cookie_from_response` 的本体，
    也是效应重放的落地入口——子进程带回的只能是 dict，不是 Response）。"""
    if not write_allowed():
        return False
    if record_effect("store_cookie", {"url": url, "cookies": new}):
        return True
    domain = host_of(url)
    with LOCK:
        row = load()["hosts"].setdefault(domain, {})
        if _cookie_banned(row):
            return False
        if row.get("cookie_failed_at"):
            row.pop("cookie_failed_at", None)   # 防抖到期自清（没被拦住还留着标记 = 已过期）
        now = datetime.now().strftime(TS_FMT)
        ck = row.get("cookie") if isinstance(row.get("cookie"), dict) else {}
        merged = parse_cookie_header(ck.get("value", "")) if ck.get("value") else {}
        merged.update(new)
        ck["value"] = _header_of(merged)
        ck.setdefault("added", now)
        ck["harvested"] = now
        row["cookie"] = ck
        row["updated"] = now
        save(domain)
    return True
