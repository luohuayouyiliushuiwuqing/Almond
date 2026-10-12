"""主项目 Web 看板服务：Raindrop 风格书签卡片，默认监听 0.0.0.0:4000。

本文件只剩：路由表合并、薄 Handler（dispatch + 兜底 + 访问日志）、启动入口。
每个资源的处理器住在同目录 `api_*.py` 里（ROUTES 字典注册，签名 `fn(h, qs, body)`）；
静态文件由 `staticfiles.serve` 自解析。api_* **禁止 import 本模块**（防环）。

用法：python -m almond.web.webui.server [--host 0.0.0.0] [--port 4000]   （在仓库根目录执行）
     或安装后直接 almond-webui
"""

import multiprocessing
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from almond.core.log import logger
from almond.core.proxy import REFRESH_INTERVAL, start_proxy_refresh
from almond.web.webui import api_fetch, api_records, api_domains, api_proxy, api_storage
from almond.web.webui import staticfiles
from almond.web.webui.api_util import read_json, send_json

# 路由表：(method, path) → handler(h, qs, body)；未命中的 GET 走静态文件，其余 404
_ROUTES: dict = {}
for _m in (api_fetch, api_records, api_domains, api_proxy, api_storage):
    _ROUTES.update(_m.ROUTES)


class Handler(BaseHTTPRequestHandler):
    # ---------- 分发 ----------

    def _safe(self, method: str) -> None:
        """处理器统一兜底：任何异常回 500 JSON，而不是直接掐断连接。"""
        try:
            self._dispatch(method)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端提前断开是常态（图片超时会被浏览器取消）——
            # 放出去只会让 socketserver 打一整段 traceback，这里一行带过
            self.log_message("client disconnected")
        except Exception as exc:
            logger.exception(f"处理 {self.command} {self.path} 时异常：{exc}")
            try:
                send_json(self, {"ok": False, "error": str(exc)}, 500)
            except Exception:
                pass

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        body: dict = {}
        if method in ("POST", "PATCH"):
            # 先取完请求体再路由：未命中 404 时不留半截 body 坑下一次 keep-alive 请求
            body = read_json(self)
            if body is None:
                send_json(self, {"ok": False, "error": "请求体不是合法 JSON"}, 400)
                return
        fn = _ROUTES.get((method, path))
        if fn is not None:
            fn(self, qs, body)
            return
        if method == "GET":
            staticfiles.serve(self, path)      # 命中写 200，未命中自回 404
            return
        self.send_error(404)

    def do_GET(self) -> None:
        self._safe("GET")

    def do_POST(self) -> None:
        self._safe("POST")

    def do_PATCH(self) -> None:
        self._safe("PATCH")

    def do_DELETE(self) -> None:
        self._safe("DELETE")

    def log_message(self, fmt: str, *args) -> None:
        # 访问日志走 DEBUG：补抓轮询每 10s 打一次，默认 INFO 下不刷屏
        logger.debug(f"{self.address_string()} {fmt % args}")


def _opt(name: str, default: str) -> str:
    """取 `--name value` 形式的参数；缺失或没跟值时回退默认值。"""
    if name not in sys.argv:
        return default
    idx = sys.argv.index(name)
    return sys.argv[idx + 1] if idx + 1 < len(sys.argv) else default


def main() -> None:
    # spawn 拉起抓取子进程时会重入本模块：freeze_support 让子进程在此短路退出，
    # 不会把 Web 服务再启动一遍（console 入口没有 __main__ 保护时尤其需要）
    multiprocessing.freeze_support()
    host = _opt("--host", "0.0.0.0")
    port = int(_opt("--port", "4000"))
    server = ThreadingHTTPServer((host, port), Handler)
    logger.info(f"Web 看板已启动：http://{host}:{port}/")
    if host == "0.0.0.0":
        logger.info(f"本机访问：http://127.0.0.1:{port}/")
        logger.info("已监听所有网卡且无鉴权；仅本机使用请加 --host 127.0.0.1")
    # 后台线程每 30s 重探一次本机代理端口（7889-7899），保持「当前代理」新鲜：
    # 代理客户端换端口重启后无需人工干预，结论变化时记日志；首轮立即执行，
    # 打的就是原先启动时那行「代理探测（7889-7899）：…」
    start_proxy_refresh()
    logger.info(f"代理自动刷新已启动：每 {REFRESH_INTERVAL}s 重探 7889-7899")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
