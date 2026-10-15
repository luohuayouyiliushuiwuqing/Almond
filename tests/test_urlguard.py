"""urlguard：抓取目标必须公网可路由。

用例一律走**字面 IP**，不依赖 DNS——CI/离线机器上域名解析的结果不该决定测试结论。
`*.invalid`（RFC 2606 保留域）用来验「解析失败时放行」这条：它永远解析不出，且无需网络。
"""

import os
import unittest
from unittest import mock

from almond.core import urlguard

BLOCKED = [
    "http://169.254.169.254/latest/meta-data/",   # 云主机元数据端点，最值钱的一个
    "http://127.0.0.1:4000/api/records",          # 自己打自己
    "http://10.0.0.5/internal",
    "http://192.168.1.1/admin",
    "http://172.16.0.1/dashboard",
    "http://[::1]/x",
    "http://0.0.0.0/x",
    "http://224.0.0.1/x",                          # 组播
    "http://240.0.0.1/x",                          # 保留
]

ALLOWED = [
    "http://8.8.8.8/",
    "https://1.1.1.1/dns-query",
    "https://93.184.216.34:443/index.html",        # example.com 曾经的字面地址
]


class TestBlockedTargets(unittest.TestCase):
    def test_private_and_special_ranges_rejected(self):
        for url in BLOCKED:
            with self.subTest(url=url):
                msg = urlguard.require_public_url(url)
                self.assertIsNotNone(msg, f"应当拦下：{url}")
                self.assertIn("不在允许范围", msg)

    def test_non_http_scheme_rejected(self):
        for url in ["ftp://1.1.1.1/file", "file:///etc/passwd", "gopher://8.8.8.8/", ""]:
            with self.subTest(url=url):
                self.assertIn("http://", urlguard.require_public_url(url))

    def test_missing_host_rejected(self):
        for url in ["http://", "http:/x", "https://:8080/"]:
            with self.subTest(url=url):
                self.assertIsNotNone(urlguard.require_public_url(url))


class TestAllowedTargets(unittest.TestCase):
    def test_public_literals_pass(self):
        for url in ALLOWED:
            with self.subTest(url=url):
                self.assertIsNone(urlguard.require_public_url(url), url)


class TestUnresolvableFallsThrough(unittest.TestCase):
    def test_dns_failure_is_not_a_security_rejection(self):
        """解析不出地址 → 放行。那是「域名不存在」，该由抓取去报错，不该伪装成权限问题。"""
        self.assertIsNone(urlguard.require_public_url("http://no-such-host.invalid/x"))


class TestEscapeHatch(unittest.TestCase):
    def test_env_allows_private_targets(self):
        with mock.patch.dict(os.environ, {"ALMOND_ALLOW_PRIVATE_URLS": "1"}):
            for url in BLOCKED:
                with self.subTest(url=url):
                    self.assertIsNone(urlguard.require_public_url(url))

    def test_env_hatch_still_enforces_scheme(self):
        with mock.patch.dict(os.environ, {"ALMOND_ALLOW_PRIVATE_URLS": "1"}):
            self.assertIn("http://", urlguard.require_public_url("file:///etc/passwd"))

    def test_scheme_check_can_be_forced_off_hatch(self):
        """allow_private_env=False 时忽略环境变量——给「无论如何都要公网」的调用方留口子。"""
        with mock.patch.dict(os.environ, {"ALMOND_ALLOW_PRIVATE_URLS": "1"}):
            msg = urlguard.require_public_url("http://127.0.0.1/x", allow_private_env=False)
            self.assertIsNotNone(msg)

    def test_unrecognized_env_values_do_not_disable_guard(self):
        for value in ["0", "", "false", "no"]:
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"ALMOND_ALLOW_PRIVATE_URLS": value}):
                    self.assertIsNotNone(urlguard.require_public_url("http://127.0.0.1/x"))


class TestApiIntegration(unittest.TestCase):
    """两个触网入口都换成了 require_public_url。"""

    def test_fetch_module_uses_the_guard(self):
        from almond.webui import api_fetch
        self.assertIs(api_fetch.require_public_url, urlguard.require_public_url)

    def test_scheme_rejection_wording_unchanged(self):
        """原来 api_util.require_http_url 的文案保持不动——它已并进入口校验，
        用户看到的报错不该因为内部换了实现而变。"""
        self.assertEqual(
            urlguard.require_public_url("ftp://1.1.1.1/file"),
            "网址必须以 http:// 或 https:// 开头",
        )


if __name__ == "__main__":
    unittest.main()
