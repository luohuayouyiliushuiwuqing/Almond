"""proxy：规则匹配 + 判定优先级 + 显式路由 + 侧栏徽标（无网络，仅读库）。"""

import _boot  # noqa: F401
from almond.core import domains, proxy, records

GLOBAL = "http://127.0.0.1:7897"


class TestPatternMatching(_boot.AlmondTestCase):
    def test_host_only(self):
        self.assertTrue(proxy._pattern_matches("github.com", "github.com", "/"))
        self.assertFalse(proxy._pattern_matches("github.com", "www.github.com", "/"))

    def test_wildcard_host(self):
        self.assertTrue(proxy._pattern_matches("*.google.com", "www.google.com", "/"))

    def test_path_rule(self):
        self.assertTrue(proxy._pattern_matches("github.com/*", "github.com", "/foo/bar"))
        self.assertFalse(proxy._pattern_matches("github.com/*", "gitlab.com", "/foo"))


class TestMatchRule(_boot.AlmondTestCase):
    def test_with_explicit_rules(self):
        rules = [{"pattern": "*.google.com", "need_proxy": True}]
        self.assertEqual(proxy.match_rule("https://www.google.com/", rules), (True, True))
        self.assertEqual(proxy.match_rule("https://example.org/", rules), (False, False))

    def test_longest_pattern_first(self):
        records.upsert_proxy_rule("*.example.com", True)
        records.upsert_proxy_rule("blocked.example.com", False)   # 更长更具体
        hit, need = proxy.match_rule("https://blocked.example.com/")
        self.assertTrue(hit)
        self.assertFalse(need)                                     # 命中更长的那条（强制直连）


class TestEffectiveProxy(_boot.AlmondTestCase):
    def test_global_default(self):
        self.assertEqual(proxy.effective_proxy("https://foreign.io/x", GLOBAL), GLOBAL)

    def test_domain_rule_overrides_global(self):
        domains.update_domain("foreign.io", need_proxy=False)      # 强制直连
        self.assertIsNone(proxy.effective_proxy("https://foreign.io/x", GLOBAL))
        domains.update_domain("foreign.io", need_proxy=True)
        self.assertEqual(proxy.effective_proxy("https://foreign.io/x", GLOBAL), GLOBAL)

    def test_url_rule_overrides_domain(self):
        domains.update_domain("foreign.io", need_proxy=True)
        records.upsert_proxy_rule("foreign.io/*", False)           # URL 规则说直连
        self.assertIsNone(proxy.effective_proxy("https://foreign.io/x", GLOBAL))


class TestResolveProxy(_boot.AlmondTestCase):
    def test_domestic_bypass(self):
        records.upsert_proxy_rule("foo.cn/*", True)                # 规则说走代理
        self.assertIsNone(proxy.resolve_proxy("https://foo.cn/x", GLOBAL))  # 国内旁路 → 直连

    def test_foreign_uses_proxy(self):
        self.assertEqual(proxy.resolve_proxy("https://foreign.io/x", GLOBAL), GLOBAL)


class TestForcedRoute(_boot.AlmondTestCase):
    def test_none_when_no_rule(self):
        self.assertIsNone(proxy.forced_route("https://foreign.io/x"))

    def test_domain_rule(self):
        domains.update_domain("forced.com", need_proxy=True)
        self.assertEqual(proxy.forced_route("https://forced.com/x"), "proxy")
        domains.update_domain("forced.com", need_proxy=False)
        self.assertEqual(proxy.forced_route("https://forced.com/x"), "direct")

    def test_url_rule_overrides_domain(self):
        domains.update_domain("forced.com", need_proxy=True)
        records.upsert_proxy_rule("forced.com/*", False)
        self.assertEqual(proxy.forced_route("https://forced.com/x"), "direct")


class TestListProxyDomains(_boot.AlmondTestCase):
    def test_only_rule_proxied(self):
        domains.update_domain("proxied.com", need_proxy=True)
        records.upsert_record({"url": "https://plain.com/x", "title": "", "thumbnail": "",
                               "favicon": "", "success": True, "details": []})
        out = proxy.list_proxy_domains()
        self.assertIn("proxied.com", out)
        self.assertNotIn("plain.com", out)


if __name__ == "__main__":
    import unittest
    unittest.main()
