"""webui 静态文件解析：URL 路径 → ROOT 下的真实文件。

原来 `server.py` 用一张写死的 `STATIC_FILES` 白名单，每加一个文件都要改代码；
前端拆成 `js/` 多个模块后改成按规则解析，放行范围仍然收得很紧：
顶层 html/css/js，或白名单子目录（当前只有 `js/`）下的一层文件。
"""

from pathlib import Path

ROOT = Path(__file__).parent  # 静态文件与本模块同目录，与是否安装无关

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}

_ALLOWED_DIRS = {"js"}  # 允许的二级目录白名单


def resolve_static(path: str) -> Path | None:
    """URL 路径 → ROOT 下的静态文件；越界/未知后缀/不存在一律 None（调用方回 404）。

    不做 percent-decode：`/js/%2e%2e/server.py` 里的 `%2e%2e` 既不会撞上字面 `..`
    判断，磁盘上也不存在这个文件名，最终 is_file() 为假 → 404。
    """
    if path in ("/", "/index.html"):
        rel = "index.html"
    else:
        if "\\" in path or ".." in path:      # 裸 .. 与反斜杠直接拒
            return None
        rel = path[1:] if path.startswith("/") else path
        parts = rel.split("/")
        if len(parts) > 2 or any(p in ("", ".", "..") for p in parts):
            return None                        # 深度 >2、空段、`.`/`..` 段
        if len(parts) == 2 and parts[0] not in _ALLOWED_DIRS:
            return None                        # 二级目录只放行 js/
    candidate = ROOT / rel
    if candidate.suffix not in CONTENT_TYPES or not candidate.is_file():
        return None                            # 未知后缀 / 不存在
    if not candidate.resolve().is_relative_to(ROOT.resolve()):
        return None                            # 符号链接逃逸的双保险
    return candidate


def serve(h, url_path: str) -> None:
    """静态文件响应：`resolve_static` 命中就写 200，未命中回 404（响应写入也归本模块）。"""
    file_path = resolve_static(url_path)
    if file_path is None:
        h.send_error(404)
        return
    body = file_path.read_bytes()
    h.send_response(200)
    h.send_header("Content-Type", CONTENT_TYPES[file_path.suffix])
    h.send_header("Content-Length", str(len(body)))
    # 静态文件禁缓存：改完代码浏览器总能拿到新版（否则旧 tab 会一直跑旧 JS）
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)
