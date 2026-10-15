"""主项目 Web 看板服务：Raindrop 风格书签卡片，默认监听 127.0.0.1:4000。

本文件只剩：路由表合并、薄 Handler（鉴权闸 + dispatch + 兜底 + 访问日志）、启动入口。
每个资源的处理器住在同目录 `api_*.py` 里（ROUTES 字典注册，签名 `fn(h, qs, body)`）；
静态文件由 `staticfiles.serve` 自解析，访问控制在 `auth.py`。api_* **禁止 import 本模块**（防环）。

鉴权口径：token 由 `--token` 或环境变量 `ALMOND_TOKEN` 给出（后者不进 `ps`，更推荐）；
**绑非回环地址又没配 token 一律拒绝启动**，不写日志警告了事过。

用法：python -m almond.webui.server [--host 127.0.0.1] [--port 4000] [--token <值>]
     或安装后直接 almond-webui
"""

import multiprocessing
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from almond.core.log import logger
from almond.core.proxy import REFRESH_INTERVAL, start_proxy_refresh
from almond.webui import api_fetch, api_records, api_domains, api_proxy, api_storage, auth
from almond.webui import staticfiles
from almond.webui.api_util import read_json, send_json

# 路由表：(method, path) → handler(h, qs, body)；未命中的 GET 走静态文件，其余 404
_ROUTES: dict = {}
for _m in (api_fetch, api_records, api_domains, api_proxy, api_storage):
    _ROUTES.update(_m.ROUTES)
# 登录/登出属于鉴权自己，不归属任何资源模块，注册表就留在装配处
_ROUTES[("GET", "/api/login")] = auth.handle_login
_ROUTES[("POST", "/api/logout")] = auth.handle_logout

# 换 Cookie 的入口本身必须能在「还没有 Cookie」时被访问
_PUBLIC_API = "/api/login"


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
            # （鉴权闸也因此必须摆在读体**之后**，否则 401 会留下未读的 body 打断复用连接）
            body = read_json(self)
            if body is None:
                send_json(self, {"ok": False, "error": "请求体不是合法 JSON"}, 400)
                return
        if path.startswith("/api/") and path != _PUBLIC_API and not auth.is_authenticated(self):
            send_json(self, {
                "ok": False, "unauthorized": True,
                "error": "未授权：浏览器请在地址后加 ?token=…，脚本/插件请带 X-Token 头",
            }, 401)
            return
        fn = _ROUTES.get((method, path))
        if fn is not None:
            fn(self, qs, body)
            return
        if method == "GET":
            # 带 ?token= 的静态页：先换 Cookie 再 302 回干净地址，前端一行代码都不用写
            if auth.grant_from_query(self, qs):
                return
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


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1") or host.startswith("127.")


def main() -> None:
    # spawn 拉起抓取子进程时会重入本模块：freeze_support 让子进程在此短路退出，
    # 不会把 Web 服务再启动一遍（console 入口没有 __main__ 保护时尤其需要）
    multiprocessing.freeze_support()
    host = _opt("--host", "127.0.0.1")
    port = int(_opt("--port", "4000"))
    # CLI 上的 token 会进 ps 与 shell history，能用环境变量就用环境变量
    token = _opt("--token", "") or os.environ.get("ALMOND_TOKEN", "")
    if not _is_loopback(host) and not token.strip():
        # fail-closed：过去这里只打一行「已监听所有网卡且无鉴权」的警告，
        # 而漏看那行警告的代价是整个库被删、服务器被当成内网请求代理
        logger.error(
            f"拒绝启动：--host {host} 是对外监听，但没有配置 token。"
            "未鉴权的看板意味着 DELETE /api/record 谁都能删库、"
            "POST /api/fetch 与 GET /api/img 是现成 SSRF。"
            "仅本机使用请回默认 --host 127.0.0.1；确实要对外就加 --token <值> 或设 ALMOND_TOKEN。"
        )
        sys.exit(2)
    auth.configure(token)

    server = ThreadingHTTPServer((host, port), Handler)
    logger.info(f"Web 看板已启动：http://{host}:{port}/")
    logger.info(f"本机访问：http://127.0.0.1:{port}/app/")
    if auth.enabled():
        # 不回显 token 本身：它可能和运维在别处复用的字符串是同一个
        logger.info("已启用 token 鉴权：浏览器地址后加 ?token=…（换 Cookie），脚本/插件带 X-Token 头")
    else:
        logger.info("未启用鉴权（仅回环监听）；要对外监听需配 --token / ALMOND_TOKEN")
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
