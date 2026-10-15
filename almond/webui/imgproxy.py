"""图片代抓：磁盘缓存 → 失败负缓存 → 代理/直连降级，返回 (status, body, ctype)。

原先是 `Handler.handle_img` 的方法体。这里只负责「取到字节」，
**不碰 socket** —— 写响应头/状态码仍留在 Handler（`_send_img`），
所以代理地址由调用方解析好传进来，本模块不 import server，避免循环。
"""

import threading
import time
from hashlib import sha256

from curl_cffi.requests import get as http_get

from almond.core.storage import get_cache_dir

# 单次尝试秒数：代理挂起时要快点认输，直连给慢 CDN 留余量
IMG_TIMEOUT_PROXY = 5
IMG_TIMEOUT = 8
# 失败负缓存 TTL——浏览器每次 render 都会重建 <img>，没有这层的话
# 一张挂掉的图会反复拖住工作线程（实测一次 5~10s）
IMG_FAIL_TTL = 60
_IMG_FAIL: dict[str, tuple[float, int, bytes, str]] = {}  # digest -> (失效时刻, status, body, ctype)
_IMG_LOCK = threading.Lock()   # ThreadingHTTPServer 多线程共享 _IMG_FAIL，读写清全靠它


def _img_try(src: str, proxy: str | None) -> tuple[int, bytes, str]:
    """一次图片请求，返回 (status, body, content-type)；失败抛异常交给调用方降级。"""
    resp = http_get(src, proxy=proxy, timeout=IMG_TIMEOUT_PROXY if proxy else IMG_TIMEOUT)
    body = resp.content
    ctype = (resp.headers.get("content-type") or "application/octet-stream").split(";")[0]
    status = resp.status_code if 100 <= (resp.status_code or 0) < 600 else 502
    return status, body, ctype


def _remember_img_fail(digest: str, status: int, body: bytes, ctype: str) -> None:
    with _IMG_LOCK:
        if len(_IMG_FAIL) > 400:  # 顺手清掉过期的，别无限涨
            now = time.monotonic()
            for k in [k for k, v in _IMG_FAIL.items() if v[0] <= now]:
                del _IMG_FAIL[k]
        _IMG_FAIL[digest] = (time.monotonic() + IMG_FAIL_TTL, status, body[:8192], ctype)


def handle_img(src: str, proxy: str | None) -> tuple[int, bytes, str]:
    """取一张图：命中本地缓存直接回；否则代理优先、失败回退直连。

    - `proxy` 是调用方用 `proxy.resolve_proxy` 解析好的地址（None = 直连，
      国内站点旁路已在解析时做完），本模块信任传入值、不再自行判定
    - 成功（2xx）落磁盘缓存；失败进 60s 负缓存，重复请求直接回、不再干等超时
    - 返回 (status, body, content-type)，不写 socket
    """
    digest = sha256(src.encode("utf-8")).hexdigest()[:32]
    cache_dir = get_cache_dir()  # 跟随存储目录，切换后自动指向新位置
    bin_path = cache_dir / f"{digest}.bin"
    ct_path = cache_dir / f"{digest}.ct"

    # 命中本地缓存：直接返回，不走网络
    if bin_path.is_file() and ct_path.is_file():
        return 200, bin_path.read_bytes(), ct_path.read_text(encoding="utf-8").strip()

    # 负缓存：短时间内失败过的直接回，不让线程再干等一次超时
    now = time.monotonic()
    with _IMG_LOCK:
        failed = _IMG_FAIL.get(digest)
    if failed and failed[0] > now:
        return failed[1], failed[2], failed[3]

    status, body, ctype = 502, b"", "text/plain"
    if proxy:
        try:
            status, body, ctype = _img_try(src, proxy)
        except Exception:
            status = 502   # 代理挂起（实测会 hang 满超时）→ 下面试直连
        if not (200 <= status < 300):
            # 异常或代理吐了错误页，都再给直连一次机会；
            # 直连也失败就保留代理那次结果（不覆盖成更差的 502）
            try:
                status, body, ctype = _img_try(src, None)
            except Exception:
                pass
    else:
        try:
            status, body, ctype = _img_try(src, None)
        except Exception:
            pass

    # 只缓存成功的图片（4xx/5xx 不落盘，避免把错误页缓存住）
    if 200 <= status < 300 and body:
        cache_dir.mkdir(parents=True, exist_ok=True)
        bin_path.write_bytes(body)
        ct_path.write_text(ctype, encoding="utf-8")
        with _IMG_LOCK:
            _IMG_FAIL.pop(digest, None)
    else:
        _remember_img_fail(digest, status, body, ctype)

    return status, body, ctype


def send_img(h, body: bytes, ctype: str, status: int = 200) -> None:
    """写图片响应（缓存策略随图片：只给 2xx 发长缓存）。"""
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    # 只有成功才允许浏览器缓存一天：给 4xx/5xx 也发 max-age=86400 的话，
    # 一次超时会让缩略图一整天都修不好（浏览器不再重试）
    h.send_header(
        "Cache-Control", "public, max-age=86400" if 200 <= status < 300 else "no-store"
    )
    h.end_headers()
    h.wfile.write(body)
