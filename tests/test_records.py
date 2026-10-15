"""records：合并规则 / 分页 / pending / 快照导入 / 标签 / 纪元守卫 / 代理规则 CRUD。"""

import _boot  # noqa: F401
from almond.core import records, storage


def _meta(url, **kw):
    base = {"url": url, "title": kw.pop("title", "T"), "thumbnail": kw.pop("thumbnail", ""),
            "favicon": kw.pop("favicon", ""), "success": kw.pop("success", True),
            "details": kw.pop("details", [])}
    base.update(kw)
    return base


class TestUpsert(_boot.AlmondTestCase):
    def test_insert_new(self):
        rec = records.upsert_record(_meta("https://a.com/x", title="A", thumbnail="https://a.com/t.png"))
        self.assertEqual(rec["title"], "A")
        self.assertTrue(rec["fetched"])
        self.assertTrue(rec["success"])
        self.assertEqual(rec["domain"], "a.com")

    def test_failed_does_not_overwrite_but_marks_fetched(self):
        records.upsert_record(_meta("https://a.com/x", title="A", thumbnail="OLD"))
        rec = records.upsert_record(_meta("https://a.com/x", title="NEW", thumbnail="NEWIMG",
                                          success=False))
        self.assertEqual(rec["title"], "A")            # 失败不覆盖内容
        self.assertEqual(rec["thumbnail"], "OLD")
        self.assertTrue(rec["fetched"])                # 但置「已尝试」
        self.assertTrue(rec["success"])                # 已成功的旧行不被失败重抓降级

    def test_failed_new_url_still_inserts_row(self):
        rec = records.upsert_record(_meta("https://b.com/y", title="", success=False))
        self.assertFalse(rec["success"])
        self.assertTrue(rec["fetched"])
        self.assertEqual(records.count_records(), 1)

    def test_empty_title_preserves_old(self):
        records.upsert_record(_meta("https://a.com/x", title="A"))
        rec = records.upsert_record(_meta("https://a.com/x", title=""))
        self.assertEqual(rec["title"], "A")


class TestPagination(_boot.AlmondTestCase):
    def _seed(self, n):
        for i in range(n):
            records.upsert_record(_meta(f"https://s{i}.com/x"))

    def test_limit_offset_slices(self):
        self._seed(5)
        self.assertEqual(records.count_records(), 5)
        self.assertEqual(len(records.list_records(2, 0)), 2)
        self.assertEqual(len(records.list_records(2, 2)), 2)
        self.assertEqual(len(records.list_records(2, 4)), 1)

    def test_pages_disjoint_and_ordered(self):
        self._seed(7)
        p1 = records.list_records(3, 0)
        p2 = records.list_records(3, 3)
        self.assertEqual([r["url"] for r in p1] + [r["url"] for r in p2],
                         [r["url"] for r in records.list_records()[:6]])

    def test_default_returns_all(self):
        self._seed(4)
        self.assertEqual(len(records.list_records()), 4)


class TestPending(_boot.AlmondTestCase):
    def test_only_unfetched(self):
        records.insert_quick_records([{"url": "https://p1.com/", "title": "1"},
                                      {"url": "https://p2.com/", "title": "2"}])
        records.upsert_record(_meta("https://p1.com/", title="fetched"))   # fetched=1
        pend = records.list_pending_urls()
        self.assertEqual(pend, ["https://p2.com/"])

    def test_quick_insert_counts(self):
        res = records.insert_quick_records([{"url": "https://q.com/", "title": "Q"}])
        self.assertEqual(res, {"inserted": 1, "skipped": 0})
        res2 = records.insert_quick_records([{"url": "https://q.com/"}])   # 已存在跳过
        self.assertEqual(res2, {"inserted": 0, "skipped": 1})
        self.assertFalse(records.list_records()[0]["fetched"])             # 快照默认未抓取


class TestTitleDelete(_boot.AlmondTestCase):
    def test_set_title(self):
        records.upsert_record(_meta("https://a.com/x", title="A"))
        rec = records.set_title("https://a.com/x", "  B  ")
        self.assertEqual(rec["title"], "B")
        self.assertIsNone(records.set_title("https://missing.com/", "x"))

    def test_set_empty_title_ok(self):
        records.upsert_record(_meta("https://a.com/x", title="A"))
        rec = records.set_title("https://a.com/x", "")
        self.assertEqual(rec["title"], "")

    def test_delete(self):
        records.upsert_record(_meta("https://a.com/x"))
        self.assertTrue(records.delete_record("https://a.com/x"))
        self.assertFalse(records.delete_record("https://a.com/x"))


class TestTags(_boot.AlmondTestCase):
    def test_tag_lifecycle(self):
        records.upsert_record(_meta("https://a.com/x"))
        tag = records.create_tag("工作")
        self.assertTrue(records.add_tag_to_record("https://a.com/x", tag["id"]))
        rec = records.list_records()[0]
        self.assertEqual([t["name"] for t in rec["tags"]], ["工作"])
        records.remove_tag_from_record("https://a.com/x", tag["id"])
        self.assertEqual(records.list_records()[0]["tags"], [])
        self.assertTrue(records.delete_tag(tag["id"]))
        self.assertEqual(records.list_tags(), [])


class TestEpochGuard(_boot.AlmondTestCase):
    def test_if_epoch_matching_writes(self):
        e = storage.storage_epoch()
        rec = records.upsert_record_if_epoch(_meta("https://a.com/x"), e)
        self.assertIsNotNone(rec)
        self.assertEqual(records.count_records(), 1)

    def test_if_epoch_mismatch_returns_none(self):
        rec = records.upsert_record_if_epoch(_meta("https://a.com/x"), storage.storage_epoch() + 999)
        self.assertIsNone(rec)
        self.assertEqual(records.count_records(), 0)


class TestProxyRules(_boot.AlmondTestCase):
    def test_crud_and_order_by_length_desc(self):
        records.upsert_proxy_rule("github.com", True)
        records.upsert_proxy_rule("*.google.com", True)
        rules = records.list_proxy_rules()
        self.assertEqual([r["pattern"] for r in rules], ["*.google.com", "github.com"])  # 长模式在前
        records.upsert_proxy_rule("github.com", False)                                     # 同 pattern 更新
        g = [r for r in records.list_proxy_rules() if r["pattern"] == "github.com"][0]
        self.assertFalse(g["need_proxy"])
        rid = g["id"]
        self.assertTrue(records.delete_proxy_rule(rid))

    def test_proxy_decision_inputs(self):
        records.upsert_record(_meta("https://rec.com/x"))
        records.upsert_proxy_rule("*.google.com", True)
        inputs = records.proxy_decision_inputs()
        self.assertIn("rec.com", inputs["names"])
        self.assertIn("*.google.com", [r["pattern"] for r in inputs["rules"]])
        self.assertIsInstance(inputs["domain_need"], dict)


if __name__ == "__main__":
    import unittest
    unittest.main()
