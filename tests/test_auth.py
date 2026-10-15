"""鉴权闸：真实 HTTP 打 Handler，验 401 / Cookie 换取 / X-Token / keep-alive 复用。

不 stub Handler：闸长在 `_dispatch` 里，而「401 必须先读完请求体」这条正是 keep-alive
层面的约束——stub 出来的对象没有连接复用，也就永远测不到最容易坏的那一环。
"""

import hashlib
import http.client
import json
import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

import _boot  # noqa: F401  必须在 import almond 之前引导隔离目录
from almond.webui import auth, server

TOKEN = "sekret-token"
# 不碰数据库也能定出结果的端点：缺 job_id 时恒回 400，能过闸就说明不是 401
PROBE = "/api/fetch/status"


def session_of(secret: str) -> str:
    return hashlib.sha256(f"almond-session:{secret}".encode("utf-8")).hexdigest()


class GateCase(_boot.AlmondTestCase):
    """起一份真服务（随机端口、回环监听），每个用例独占。"""

    def setUp(self):
        super().setUp()
        auth.configure(TOKEN)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)
        auth.configure(None)
        super().tearDown()

    def request(self, method, path, headers=None, body=None, conn=None):
        """发一次请求并读完响应体；conn 传入则复用该连接（验 keep-alive 用）。"""
        own = conn is None
        c = conn or http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=body, headers=headers or {})
        resp = c.getresponse()
        payload = resp.read()
        if own:
            c.close()
        return resp, payload

    def json_of(self, payload):
        return json.loads(payload.decode("utf-8"))


class TestApiGate(GateCase):
    def test_no_credential_is_401(self):
        resp, payload = self.request("GET", PROBE)
        self.assertEqual(resp.status, 401)
        self.assertTrue(self.json_of(payload)["unauthorized"])

    def test_x_token_header_passes(self):
        resp, _ = self.request("GET", PROBE, {"X-Token": TOKEN})
        self.assertEqual(resp.status, 400)   # 过了闸，剩下的只是「缺 job_id」

    def test_wrong_x_token_is_401(self):
        resp, _ = self.request("GET", PROBE, {"X-Token": TOKEN + "x"})
        self.assertEqual(resp.status, 401)

    def test_session_cookie_passes(self):
        resp, _ = self.request("GET", PROBE, {"Cookie": f"{auth.COOKIE_NAME}={session_of(TOKEN)}"})
        self.assertEqual(resp.status, 400)

    def test_raw_token_in_cookie_does_not_pass(self):
        """Cookie 里放的是派生值；把 token 原文塞进 Cookie 不该被当成有效会话。"""
        resp, _ = self.request("GET", PROBE, {"Cookie": f"{auth.COOKIE_NAME}={TOKEN}"})
        self.assertEqual(resp.status, 401)

    def test_disabled_gate_passes_everything(self):
        auth.configure(None)
        resp, _ = self.request("GET", PROBE)
        self.assertEqual(resp.status, 400)


class TestLogin(GateCase):
    def test_login_sets_httponly_cookie_and_redirects(self):
        resp, _ = self.request("GET", f"/api/login?token={TOKEN}")
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.getheader("Location"), "/app/")
        cookie = resp.getheader("Set-Cookie")
        self.assertIn(f"{auth.COOKIE_NAME}={session_of(TOKEN)}", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("Secure", cookie)          # 明文回环连接上加 Secure 会被丢掉

    def test_login_behind_https_proxy_gets_secure(self):
        resp, _ = self.request("GET", f"/api/login?token={TOKEN}",
                               {"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", resp.getheader("Set-Cookie"))

    def test_login_with_wrong_token_is_401(self):
        resp, payload = self.request("GET", "/api/login?token=nope")
        self.assertEqual(resp.status, 401)
        self.assertFalse(self.json_of(payload)["ok"])

    def test_issued_cookie_actually_authenticates(self):
        """闭环：先换 Cookie，再拿这个 Cookie 过闸。"""
        resp, _ = self.request("GET", f"/api/login?token={TOKEN}")
        value = resp.getheader("Set-Cookie").split(";")[0]
        resp2, _ = self.request("GET", PROBE, {"Cookie": value})
        self.assertEqual(resp2.status, 400)


class TestStaticTokenBootstrap(GateCase):
    def test_page_url_with_token_swaps_cookie_and_strips_query(self):
        resp, _ = self.request("GET", f"/app/?token={TOKEN}")
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.getheader("Location"), "/app/")   # token 不留址栏
        self.assertIn(f"{auth.COOKIE_NAME}={session_of(TOKEN)}", resp.getheader("Set-Cookie"))

    def test_page_itself_is_never_gated(self):
        """静态页放行：没 token 也该能看到页面、再被引导去取 token（数据面才收口）。"""
        resp, _ = self.request("GET", "/app/")
        self.assertEqual(resp.status, 200)

    def test_wrong_token_in_query_leaves_request_to_normal_flow(self):
        resp, _ = self.request("GET", "/app/?token=nope")
        self.assertEqual(resp.status, 200)          # 没种 Cookie，照常出页
        self.assertIsNone(resp.getheader("Set-Cookie"))


class TestKeepAliveAfterReject(GateCase):
    def test_401_on_post_does_not_poison_the_connection(self):
        """401 前必须把 body 读干净，否则同一连接的下一次请求会解析到半截 body。"""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            resp, _ = self.request("POST", "/api/fetch",
                                   {"Content-Type": "application/json"},
                                   body=json.dumps({"url": "https://example.com/"}), conn=conn)
            self.assertEqual(resp.status, 401)
            resp2, _ = self.request("GET", f"/api/login?token={TOKEN}", conn=conn)
            self.assertEqual(resp2.status, 302)     # 连接还健康
        finally:
            conn.close()


class TestLoopbackPolicy(unittest.TestCase):
    def test_loopback_forms(self):
        for host in ("127.0.0.1", "localhost", "::1", "127.5.6.7"):
            self.assertTrue(server._is_loopback(host), host)
        for host in ("0.0.0.0", "192.168.1.10", "10.0.0.1"):
            self.assertFalse(server._is_loopback(host), host)

    def test_public_bind_without_token_refuses_to_start(self):
        """fail-closed：对外监听却没配 token，必须拒绝启动而不是打一行警告。"""
        argv = ["server", "--host", "0.0.0.0"]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.dict(os.environ, {"ALMOND_TOKEN": ""}):
            with self.assertRaises(SystemExit) as caught:
                server.main()
        self.assertEqual(caught.exception.code, 2)


class TestTokenSources(GateCase):
    def test_header_wins_over_stale_cookie(self):
        """两个凭证同时存在时不因 Cookie 过期而误拒（浏览器会照发旧 Cookie）。"""
        resp, _ = self.request("GET", PROBE, {
            "X-Token": TOKEN,
            "Cookie": f"{auth.COOKIE_NAME}=bogus",
        })
        self.assertEqual(resp.status, 400)


if __name__ == "__main__":
    unittest.main()
