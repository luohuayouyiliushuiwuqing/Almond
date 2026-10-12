"""本地演示服务：托管静态页面，并提供 /api/fetch 接口。

接口用 Scrapling 抓取指定网址，再按 CSS 选择器提取元素，供试验台展示。
抓取逻辑复用 almond.core 的 fetcher / fields。

用法：python -m almond.web.demo.server [--host 127.0.0.1] [--port 8765]   （在仓库根目录执行）
     或安装后直接 almond-demo
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from almond.core.fields import extract_fields
from almond.core.fetcher import fetch_page
from almond.core.log import logger

ROOT = Path(__file__).parent  # 静态文件与本 server.py 同目录，与是否安装无关

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
MAX_HTML_CHARS = 400_000  # 超大页面不回传源码，只回传提取结果
MAX_ITEMS = 30


def _collapse(text: str) -> str:
    return " ".join((text or "").split())


def extract_items(elements) -> tuple[list[dict], int]:
    """把匹配元素转成展示用条目，返回 (条目列表, 总匹配数)。"""
    items = []
    for el in elements[:MAX_ITEMS]:
        attrib = dict(el.attrib) if getattr(el, "attrib", None) else {}
        # .text 只含直接文本子节点，需要 get_all_text() 取全部后代文本
        getter = getattr(el, "get_all_text", None)
        text = _collapse((getter() if callable(getter) else None) or (getattr(el, "text", "") or ""))
        items.append({
            "tag": el.tag if isinstance(el.tag, str) else str(el.tag),
            "classes": " ".join(attrib.get("class", "").split()),
            "text": text if len(text) <= 220 else text[:220] + "…",
            "attrs": {k: (v if len(v) <= 120 else v[:120] + "…") for k, v in list(attrib.items())[:12]},
        })
    return items, len(elements)


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/fetch":
            self.handle_fetch(parse_qs(parsed.query))
            return
        file_path = self.resolve_static(parsed.path)
        if file_path is None:
            self.send_error(404)
            return
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES[file_path.suffix])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def resolve_static(self, path: str) -> Path | None:
        """只允许访问项目根目录下的一层 html/css/js 文件，防止目录穿越。"""
        rel = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        if "/" in rel or "\\" in rel or ".." in rel:
            return None
        candidate = ROOT / rel
        if candidate.suffix not in CONTENT_TYPES or not candidate.is_file():
            return None
        return candidate

    def handle_fetch(self, qs: dict) -> None:
        url = (qs.get("url") or [""])[0].strip()
        selector = (qs.get("selector") or [""])[0].strip()
        proxy = (qs.get("proxy") or [""])[0].strip() or None
        timeout = int((qs.get("timeout") or ["15"])[0])

        if not url.startswith(("http://", "https://")):
            self._send_json({"ok": False, "error": "网址必须以 http:// 或 https:// 开头"}, 400)
            return
        if not selector:
            self._send_json({"ok": False, "error": "缺少选择器（selector）"}, 400)
            return

        response = fetch_page(url, timeout=timeout, proxy=proxy)
        if response is None:
            self._send_json({"ok": False, "error": f"三级抓取全部失败：{url}"})
            return

        try:
            elements = response.css(selector)
        except Exception as exc:
            self._send_json({"ok": False, "error": f"选择器错误：{exc}"})
            return
        items, count = extract_items(elements)
        fields = extract_fields(elements)

        html = getattr(response, "html_content", "") or ""
        payload = {
            "ok": True,
            "finalUrl": getattr(response, "url", url) or url,
            "status": getattr(response, "status", None),
            "selector": selector,
            "count": count,
            "items": items,
            "fields": fields,
            "html": html if len(html) <= MAX_HTML_CHARS else None,
        }
        self._send_json(payload)

    def log_message(self, fmt: str, *args) -> None:
        logger.debug(f"{self.address_string()} {fmt % args}")


def _opt(name: str, default: str) -> str:
    """取 `--name value` 形式的参数；缺失或没跟值时回退默认值（与 webui 同款）。"""
    if name not in sys.argv:
        return default
    idx = sys.argv.index(name)
    return sys.argv[idx + 1] if idx + 1 < len(sys.argv) else default


def main() -> None:
    host = _opt("--host", "127.0.0.1")
    port = int(_opt("--port", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    logger.info(f"演示服务已启动：http://{host}:{port}/")
    logger.info("接口：GET /api/fetch?url=<网址>&selector=<CSS选择器>&proxy=<代理，可选>")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
