"""hostutil：根域名提取 + 国内站点判定（纯函数）。"""

import _boot  # noqa: F401  必须首个 import：隔离存储目录
from almond.core.hostutil import is_domestic, root_domain


class TestRootDomain(_boot.AlmondTestCase):
    def test_plain_two_label(self):
        self.assertEqual(root_domain("https://example.com/a/b?x=1"), "example.com")

    def test_subdomain_collapses_to_registrable(self):
        self.assertEqual(root_domain("https://chat.deepseek.com/"), "deepseek.com")
        self.assertEqual(root_domain("https://a.b.c.example.org"), "example.org")

    def test_multi_tld_keeps_extra_label(self):
        # bar.co.uk 不能被截成 co.uk
        self.assertEqual(root_domain("https://bar.co.uk/x"), "bar.co.uk")
        self.assertEqual(root_domain("https://foo.com.cn/x"), "foo.com.cn")

    def test_ip_and_port(self):
        self.assertEqual(root_domain("http://127.0.0.1:8080/x"), "0.1")

    def test_single_label_and_empty(self):
        self.assertEqual(root_domain("http://localhost/x"), "localhost")
        self.assertEqual(root_domain(""), "")

    def test_case_insensitive(self):
        self.assertEqual(root_domain("https://Example.COM/x"), "example.com")


class TestIsDomestic(_boot.AlmondTestCase):
    def test_cn_suffix(self):
        self.assertTrue(is_domestic("https://foo.cn/x"))
        self.assertTrue(is_domestic("https://foo.com.cn/x"))

    def test_known_domestic_domain(self):
        self.assertTrue(is_domestic("https://www.zhihu.com/question/1"))
        self.assertTrue(is_domestic("https://mimo.mi.com/a"))   # 子域名匹配 mi.com

    def test_foreign_not_domestic(self):
        self.assertFalse(is_domestic("https://www.google.com/search"))
        self.assertFalse(is_domestic("https://github.com/x"))

    def test_empty_is_not_domestic(self):
        self.assertFalse(is_domestic(""))


if __name__ == "__main__":
    import unittest
    unittest.main()
