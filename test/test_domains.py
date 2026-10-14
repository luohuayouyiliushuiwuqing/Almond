"""domains：三态配置读写 / prefs / 抓取规则 / 域名重置 replace_domain。"""

import _boot  # noqa: F401
from almond.core import domains, records


class TestUpdateDomain(_boot.AlmondTestCase):
    def test_create_and_defaults(self):
        d = domains.update_domain("ex.com")
        self.assertEqual(d["name"], "ex.com")
        self.assertIsNone(d["need_proxy"])       # NULL = 跟随全局
        self.assertIsNone(d["auto_fetch"])
        self.assertIsNone(d["cover"])
        self.assertEqual(d["prefs"], {})
        self.assertEqual(d["detail_selector"], "")

    def test_need_proxy_tristate(self):
        self.assertTrue(domains.update_domain("ex.com", need_proxy=True)["need_proxy"])
        self.assertIs(domains.update_domain("ex.com", need_proxy=None)["need_proxy"], None)
        self.assertIs(domains.update_domain("ex.com", need_proxy=False)["need_proxy"], False)

    def test_prefs_roundtrip(self):
        domains.update_domain("ex.com", prefs={"sort": "title", "fields": ["url"]})
        d = [x for x in domains.list_domains() if x["name"] == "ex.com"][0]
        self.assertEqual(d["prefs"], {"sort": "title", "fields": ["url"]})

    def test_prefs_must_be_dict(self):
        with self.assertRaises(ValueError):
            domains.update_domain("ex.com", prefs=["not", "a", "dict"])

    def test_unset_leaves_unchanged(self):
        domains.update_domain("ex.com", display_name="别名", auto_fetch=False)
        d = domains.update_domain("ex.com", detail_selector=".x")   # 没传的字段不动
        self.assertEqual(d["display_name"], "别名")
        self.assertIs(d["auto_fetch"], False)
        self.assertEqual(d["detail_selector"], ".x")


class TestGetters(_boot.AlmondTestCase):
    def test_get_domain_need_proxy(self):
        self.assertIsNone(domains.get_domain_need_proxy("https://ex.com/a"))   # 无行 → None
        domains.update_domain("ex.com", need_proxy=True)
        self.assertIs(domains.get_domain_need_proxy("https://sub.ex.com/a"), True)
        domains.update_domain("ex.com", need_proxy=None)
        self.assertIsNone(domains.get_domain_need_proxy("https://ex.com/a"))   # NULL → None

    def test_get_domain_fetch_config_excludes_proxy(self):
        domains.update_domain("ex.com", detail_selector=".space-y-2 > *", cover=True)
        cfg = domains.get_domain_fetch_config("https://ex.com/a")
        self.assertEqual(cfg["detail_selector"], ".space-y-2 > *")
        self.assertIs(cfg["cover"], True)
        self.assertNotIn("need_proxy", cfg)          # 故意不含代理结论

    def test_fetch_config_defaults_for_unknown(self):
        cfg = domains.get_domain_fetch_config("https://unknown.io/a")
        self.assertEqual(cfg, {"detail_selector": "", "cover": None})


class TestReplaceDomain(_boot.AlmondTestCase):
    def test_rewrite_urls_and_assets(self):
        records.upsert_record({"url": "https://old.com/a", "title": "A",
                               "thumbnail": "https://old.com/t.png", "favicon": "",
                               "success": True, "details": []})
        records.upsert_record({"url": "https://cdn.old.com/b", "title": "B",
                               "thumbnail": "", "favicon": "", "success": True, "details": []})
        rep = domains.replace_domain("old.com", "new.com")
        self.assertEqual(rep["total"], 2)
        self.assertEqual(rep["records"], 2)
        urls = {r["url"] for r in records.list_records()}
        self.assertIn("https://new.com/a", urls)
        self.assertIn("https://cdn.new.com/b", urls)             # 子域名跟着换
        a = [r for r in records.list_records() if r["url"] == "https://new.com/a"][0]
        self.assertEqual(a["thumbnail"], "https://new.com/t.png")

    def test_merge_duplicate_moves_tags(self):
        records.upsert_record({"url": "https://old.com/x", "title": "OLD", "thumbnail": "",
                               "favicon": "", "success": True, "details": []})
        records.upsert_record({"url": "https://new.com/x", "title": "NEW", "thumbnail": "",
                               "favicon": "", "success": True, "details": []})
        tag = records.create_tag("T")
        records.add_tag_to_record("https://old.com/x", tag["id"])
        rep = domains.replace_domain("old.com", "new.com")
        self.assertEqual(rep["merged"], 1)
        self.assertEqual(rep["records"], 0)
        urls = {r["url"] for r in records.list_records()}
        self.assertEqual(urls, {"https://new.com/x"})             # 旧行删除
        kept = records.list_records()[0]
        self.assertEqual([t["name"] for t in kept["tags"]], ["T"])  # 标签迁到新行

    def test_domains_row_renamed_keeps_settings(self):
        domains.update_domain("old.com", display_name="旧站", need_proxy=True)
        domains.replace_domain("old.com", "new.com")
        d = {x["name"]: x for x in domains.list_domains()}
        self.assertIn("new.com", d)
        self.assertNotIn("old.com", d)
        self.assertEqual(d["new.com"]["display_name"], "旧站")
        self.assertIs(d["new.com"]["need_proxy"], True)

    def test_proxy_rule_pattern_swapped(self):
        records.upsert_proxy_rule("old.com/*", True)
        rep = domains.replace_domain("old.com", "new.com")
        self.assertEqual(rep["rules"], 1)
        self.assertEqual([r["pattern"] for r in records.list_proxy_rules()], ["new.com/*"])

    def test_validation_errors(self):
        with self.assertRaises(ValueError):
            domains.replace_domain("", "new.com")
        with self.assertRaises(ValueError):
            domains.replace_domain("not_a_domain", "new.com")
        with self.assertRaises(ValueError):
            domains.replace_domain("old.com", "old.com")


if __name__ == "__main__":
    import unittest
    unittest.main()
