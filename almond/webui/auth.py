"""看板访问控制：一个 token，两条凭证通道。

判定只看**启动时**定下的两件事（监听地址、是否配了 token），不看单条请求来自哪个 peer ——
挂上反代以后所有请求的来源都是 127.0.0.1，按 peer 放行等于对公网全开。

两条通道各有各的必然性：
- **Cookie `almond_session`**：浏览器里的 SPA 与 `<img src>` 发的 `GET /api/img` 都只能靠它。
  图片请求加不了自定义头，这是 Cookie 存在的理由。
- **头 `X-Token`**：MV3 插件从 `chrome-extension://` 源发的是跨站请求，`SameSite=Lax` 的
  Cookie 不会随之发出，只有显式头能过（见 `extension/README.md`）。

Cookie 里放的是 token 的派生值而不是 token 原文：`sha256("almond-session:" + token)`。
落盘/被 XSS 读到时不至于把运维在别处复用的那个字符串一起带走。
"""

import hashlib
import hmac
from urllib.parse import urlparse

from almond.webui.api_util import send_json

COOKIE_NAME = "almond_session"
COOKIE_MAX_AGE = 30 * 24 * 3600   # 30 天：个人看板，不值得为此再造一套续期

_token: str | None = None


def configure(value) -> None:
    """启动时写入 token；空串/None 一律视作「不启用鉴权」。"""
    global _token
    _token = (value or "").strip() or None


def enabled() -> bool:
    return _token is not None


def session_value(secret: str) -> str:
    return hashlib.sha256(f"almond-session:{secret}".encode("utf-8")).hexdigest()


def _matches(expected: str, given: str) -> bool:
    """定长比较，避免按前缀逐字节早退的时间侧信道。"""
    if not given:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), given.encode("utf-8"))


def _cookie_value(h) -> str:
    for part in (h.headers.get("Cookie") or "").split(";"):
        key, _, val = part.strip().partition("=")
        if key == COOKIE_NAME:
            return val.strip()
    return ""


def is_authenticated(h) -> bool:
    """未配 token 时恒真（本机无鉴权是默认形态）；配了则头与 Cookie 二选一即可。"""
    if _token is None:
        return True
    if _matches(_token, (h.headers.get("X-Token") or "").strip()):
        return True
    return _matches(session_value(_token), _cookie_value(h))


def grants_token(value) -> bool:
    """校验外部提交来的 token（`?token=` 或登录体），命中即视为可换 Cookie。"""
    if _token is None:
        return True
    return _matches(_token, (value or "").strip())


def _is_https(h) -> bool:
    """反代终结 TLS 时，只有 X-Forwarded-Proto 能说明这是 https；
    明文连接上加 `Secure` 会让浏览器直接丢掉 Cookie。"""
    proto = (h.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
    return proto == "https"


def _cookie_string(value: str, max_age: int, https: bool) -> str:
    bits = [f"{COOKIE_NAME}={value}", "Path=/", f"Max-Age={max_age}", "HttpOnly", "SameSite=Lax"]
    if max_age and https:
        bits.append("Secure")
    return "; ".join(bits)


def _session_cookie(h) -> str | None:
    """待下发的会话 Cookie；未启用鉴权时为 None——没有值得种的凭证。"""
    if _token is None:
        return None
    return _cookie_string(session_value(_token), COOKIE_MAX_AGE, _is_https(h))


def _redirect(h, location: str, cookie: str | None) -> None:
    """状态线 → 头 → 空行，顺序不能颠倒：`send_header` 早于 `send_response` 会把
    状态线挤掉，客户端读到的是一行裸的 `Set-Cookie:`（这版之前就错过一次）。"""
    h.send_response(302)
    if cookie:
        h.send_header("Set-Cookie", cookie)
    h.send_header("Location", location)
    h.end_headers()


def handle_login(h, qs: dict, body: dict) -> None:
    """GET /api/login?token=… → 换会话 Cookie 并回看板（302 把 token 从地址栏带走）。"""
    if not grants_token((qs.get("token") or [""])[0]):
        send_json(h, {"ok": False, "error": "访问令牌不正确"}, 401)
        return
    _redirect(h, "/app/", _session_cookie(h))


def handle_logout(h, qs: dict, body: dict) -> None:
    """POST /api/logout → 清 Cookie（仍需先过鉴权，免得被当成免费的清 session 探针）。"""
    send_json(h, {"ok": True}, 200, [("Set-Cookie", _cookie_string("", 0, False))])


def grant_from_query(h, qs: dict) -> bool:
    """静态页 URL 上带 `?token=` 且正确时下发 Cookie，并 302 回去掉 token 的同一路径。

    让「把带 token 的链接发给自己或同事」这条路不需要任何前端代码就能成立。
    返回 True 表示本请求已处理完（调用方不要再往下走静态文件）。
    """
    given = (qs.get("token") or [""])[0]
    if not given or not grants_token(given):
        return False
    # 302 掉 query：token 不该留在地址栏、浏览器历史与反代访问日志里。
    # fragment 不用处理——浏览器根本不会把 `#` 之后的内容发给服务器。
    location = urlparse(h.path).path or "/app/"
    _redirect(h, location, _session_cookie(h))
    return True
