"""confirm：候选收集 / 决策 / 端到端（fetchpool 打桩，无网络无子进程）。"""

import unittest.mock as mock

import _boot  # noqa: F401
from almond.core import confirm, domains, fetch_hints, hints_strategy, records, storage


class TestCandidates(_boot.AlmondTestCase):
    def test_group_by_root_and_skip_autofetch_off(self):
        records.insert_quick_records([
            {"url": "https://old.com/1"}, {"url": "https://old.com/2"},
            {"url": "https://new.com/3"}, {"url": "https://skip.com/4"},
        ])
        domains.update_domain("skip.com", auto_fetch=False)
        by, probes = confirm._candidates()
        self.assertEqual(set(by["old.com"]), {"https://old.com/1", "https://old.com/2"})
        self.assertEqual(by["new.com"], ["https://new.com/3"])
        self.assertNotIn("skip.com", by)                     # auto_fetch=0 不参与
        self.assertEqual(probes, set())

    def test_wanted_without_pending_synthesizes_root_probe(self):
        by, probes = confirm._candidates(["empty.com"])
        self.assertEqual(by["empty.com"], ["https://empty.com/"])
        self.assertIn("empty.com", probes)


class TestDecide(_boot.AlmondTestCase):
    def test_ready_when_plan_exists(self):
        hints_strategy.save_strategy("https://ready.com/x", "direct|static", 10)
        self.assertEqual(confirm._decide("ready.com", ["https://ready.com/x"]), ("ready", None))

    def test_skipped_when_confirmed_failed(self):
        fetch_hints.confirm_domain("https://nf.com/x", False, "no")
        action, reason = confirm._decide("nf.com", ["https://nf.com/x"])
        self.assertEqual(action, "skipped")
        self.assertEqual(reason, "已确认无可用方案")

    def test_skipped_when_confirmed_ok_but_no_plan_now(self):
        fetch_hints.confirm_domain("https://stale.com/x", True)
        action, reason = confirm._decide("stale.com", ["https://stale.com/x"])
        self.assertEqual(action, "skipped")
        self.assertEqual(reason, "确认过但当前无方案")

    def test_test_when_no_confirm(self):
        self.assertEqual(confirm._decide("t.com", ["https://t.com/a", "https://t.com/b"]),
                         ("test", "https://t.com/a"))

    def test_skipped_when_no_urls(self):
        self.assertEqual(confirm._decide("t.com", []), ("skipped", "无可用测试网址"))

    def test_force_bypasses_confirm(self):
        fetch_hints.confirm_domain("https://nf.com/x", False, "no")
        self.assertEqual(confirm._decide("nf.com", ["https://nf.com/x"], force=True),
                         ("test", "https://nf.com/x"))


def _meta(url, ok):
    return {"url": url, "title": "OK" if ok else "IGNORED", "thumbnail": "", "favicon": "",
            "success": ok, "details": []}


class TestConfirmDomains(_boot.AlmondTestCase):
    def test_confirmed_and_failed_paths(self):
        records.insert_quick_records([{"url": "https://ok.com/x"},
                                      {"url": "https://bad.com/y", "title": "BadSite"}])

        def fake_submit(**kw):
            return kw["url"]

        def fake_wait(job):
            ok = job == "https://ok.com/x"
            return (_meta(job, ok), [])

        with mock.patch.object(confirm.fetchpool, "submit", side_effect=fake_submit), \
             mock.patch.object(confirm.fetchpool, "wait", side_effect=fake_wait):
            report = confirm.confirm_domains(
                start_epoch=storage.storage_epoch(),
                proxy_for=lambda u: None, route_for=lambda u: None)

        self.assertIn("ok.com", report["confirmed"])
        self.assertIn("bad.com", [x["domain"] for x in report["failed"]])

        recs = {r["url"]: r for r in records.list_records()}
        # ok.com：成功抓取入库
        self.assertTrue(recs["https://ok.com/x"]["success"])
        self.assertEqual(recs["https://ok.com/x"]["title"], "OK")
        # bad.com：失败重抓**不覆盖**已有内容（合并规则），仅置「已尝试」；
        #   「失败」的真实信号在 confirm 结论，而非记录 success 被降级
        self.assertEqual(recs["https://bad.com/y"]["title"], "BadSite")   # 未被 "IGNORED" 覆盖
        self.assertTrue(recs["https://bad.com/y"]["fetched"])
        self.assertTrue(recs["https://bad.com/y"]["success"])             # 旧 success 保留

        self.assertTrue(fetch_hints.get_confirm("https://ok.com/x")["ok"])
        self.assertFalse(fetch_hints.get_confirm("https://bad.com/y")["ok"])


if __name__ == "__main__":
    import unittest
    unittest.main()
