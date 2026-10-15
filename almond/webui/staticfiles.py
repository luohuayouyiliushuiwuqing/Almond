"""webui 静态文件解析：URL 路径 → ROOT 下的真实文件。

前端是 React + Vite，构建产物落在同包 `dist/`（vite base 为 `/app/`）：
`/` 与 `/app*` 都解析到 dist 里的文件，其余带资源后缀的路径一律 404。
"""

from pathlib import Path

ROOT = Path(__file__).parent  # 静态文件与本模块同目录，与是否安装无关

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}

def resolve_static(path: str) -> Path | None:
    """URL 路径 → ROOT 下的静态文件；越界/未知后缀/不存在一律 None（调用方回 404）。

    不做 percent-decode：编码后的 `..`（`%2e%2e`）既不会撞上字面 `..` 判断，磁盘上
    也不存在这个文件名，最终 is_file() 为假 → 404。
    """
    # React 应用挂载：/app → 构建产物 dist/（SPA，未知路径回退 index.html）
    if path == "/app" or path.startswith("/app/"):
        return _resolve_app(path)
    # 站点根：直接返回 React SPA 的入口（dist/index.html）。
    # 前端的资源基址是 /app/（见 vite.config.js base），因此 /app/assets/* 由上面的
    # /app 挂载解析；这里只负责把「打开根路径」这件事指到构建产物上。
    if path in ("/", "/index.html"):
        return _resolve_app("/")
    # SPA 回退：末段扩展名不在 CONTENT_TYPES 里的「页面路径」（如 /foo、
    # /domain/github.com，或 history 风格路由）一律返回 React 入口，由前端 hash
    # 路由接管——避免用户误访这类路径时看到裸 404。真正带资源后缀（.js/.css/...）
    # 且缺失的文件仍 404，避免掩盖资源加载错误；/api/* 不在此回退范围，交给 API
    # 路由按原样处理（未知接口仍 404）。
    tail = path.split("/")[-1]
    if (
        not path.startswith("/api/")
        and "\\" not in path
        and ".." not in path
        and Path(tail).suffix not in CONTENT_TYPES
    ):
        return _resolve_app("/")
    return None


def _resolve_app(path: str) -> Path | None:
    """React 应用（Vite 构建到 dist/）挂载点 /app 的解析。

    - /app 或 /app/ → dist/index.html
    - /app/assets/<hash>.js 等多级资源直接命中
    - 其余无后缀路径（如客户端路由 /app/xxx）回退到 index.html（SPA）
    """
    dist = ROOT / "dist"
    sub = path[len("/app"):]
    sub = sub[1:] if sub.startswith("/") else sub
    if "\\" in sub or ".." in sub:
        return None
    if not sub:
        candidate = dist / "index.html"
    else:
        parts = sub.split("/")
        if any(p in ("", ".", "..") for p in parts):
            return None
        candidate = dist.joinpath(*parts)
    if not candidate.is_file():
        # SPA 回退：仅对无后缀的「页面路径」回退 index.html；明确资源（.js/.css）缺失即 404
        if not candidate.suffix:
            candidate = dist / "index.html"
        if not candidate.is_file():
            return None
    if not candidate.resolve().is_relative_to(dist.resolve()):
        return None
    return candidate


def serve(h, url_path: str) -> None:
    """静态文件响应：`resolve_static` 命中就写 200，未命中回 404（响应写入也归本模块）。"""
    file_path = resolve_static(url_path)
    if file_path is None:
        h.send_error(404)
        return
    body = file_path.read_bytes()
    h.send_response(200)
    h.send_header("Content-Type", CONTENT_TYPES.get(file_path.suffix, "application/octet-stream"))
    h.send_header("Content-Length", str(len(body)))
    # 静态文件禁缓存：改完代码浏览器总能拿到新版（否则旧 tab 会一直跑旧 JS）
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)
