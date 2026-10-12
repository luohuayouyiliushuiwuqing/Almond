"""HTTP 请求/响应小助手：JSON 读写与参数校验的唯一实现。

原先散在 Handler 的 `_send_json`/`_read_json` 与各分支手写的
「网址必须以 http:// 或 https:// 开头」「缺少 x 或 y」——这里收拢一份。
api_* 处理函数拿到的 `h` 是 Handler 实例，写响应走 `send_json(h, ...)`。
"""

import json


def send_json(h, payload: dict, status: int = 200) -> None:
    """写 JSON 响应（原 Handler._send_json 的实现）。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def bad_request(h, msg: str) -> None:
    """400 + {ok: False, error: msg} 的快捷出口。"""
    send_json(h, {"ok": False, "error": msg}, 400)


def read_json(h) -> dict | None:
    """读请求体 JSON 对象。

    空体 → {}；**非法 JSON / 非对象体 → None**——调用方应答
    400「请求体不是合法 JSON」（原先吞掉异常返回 {}，handler 只能误报「缺少字段」）。
    """
    length = int(h.headers.get("Content-Length") or 0)
    if not length:
        return {}
    try:
        data = json.loads(h.rfile.read(length).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def require_http_url(value) -> str | None:
    """校验 http(s) 网址：合法返回 None，否则返回错误文案。"""
    if not (value or "").strip().startswith(("http://", "https://")):
        return "网址必须以 http:// 或 https:// 开头"
    return None


def require_fields(body: dict, *keys) -> str | None:
    """校验必填字段（None / 空串算缺，0 等合法值不算）：齐全返回 None，否则返回错误文案。"""
    missing = [k for k in keys if body.get(k) in (None, "")]
    if missing:
        return f"缺少 {' 或 '.join(missing)}"
    return None
