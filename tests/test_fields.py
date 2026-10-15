"""fields.extract_fields：「标签: 值」行解析（用轻量假元素，纯逻辑、无网络）。"""

import _boot  # noqa: F401
from almond.core.fields import MAX_FIELDS, extract_fields


class FakeText:
    """可当 span / a / time 的叶子节点。"""

    def __init__(self, text="", tag="span", attrib=None):
        self._text = text
        self.tag = tag
        self.attrib = attrib or {}
        self.children = []

    def get_all_text(self):
        return self._text


class FakeEl:
    """一行：children[0] 是 span 标签，其余是文本/链接/时间。"""

    def __init__(self, full_text, span_text, links=None, time_attrib=None, first_tag="span"):
        self._full = full_text
        self.children = [FakeText(span_text, tag=first_tag)]
        self._links = [FakeText(t, tag="a", attrib={"href": h}) for t, h in (links or [])]
        self._time = [FakeText("", tag="time", attrib=time_attrib)] if time_attrib else []

    def get_all_text(self):
        return self._full

    def css(self, selector):
        if selector == "time":
            return self._time
        if selector == "a":
            return self._links
        return []


class TestExtractFields(_boot.AlmondTestCase):
    def test_basic_label_value(self):
        el = FakeEl("发行日期：2020-01-01", "发行日期：")
        out = extract_fields([el])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["label"], "发行日期")
        self.assertEqual(out[0]["value"], "2020-01-01")
        self.assertEqual(out[0]["links"], [])
        self.assertEqual(out[0]["datetime"], "")

    def test_value_with_link_dedup(self):
        # value 原文含链接文字 → 去掉链接文字，只留 links
        el = FakeEl("标签链接文字", "标签", links=[("链接文字", "/x")])
        out = extract_fields([el])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["value"], "")
        self.assertEqual(out[0]["links"], [{"text": "链接文字", "href": "/x"}])

    def test_datetime_keeps_field_with_empty_value(self):
        el = FakeEl("时间", "时间", time_attrib={"datetime": "2020-01-01T00:00:00"})
        out = extract_fields([el])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["datetime"], "2020-01-01T00:00:00")

    def test_empty_value_no_links_no_time_is_skipped(self):
        el = FakeEl("标签", "标签")
        self.assertEqual(extract_fields([el]), [])

    def test_non_span_first_child_is_skipped(self):
        el = FakeEl("xx2020", "标签", first_tag="div")
        self.assertEqual(extract_fields([el]), [])

    def test_whitespace_collapsed(self):
        el = FakeEl("发行： 2020   01 ", "发行：")
        out = extract_fields([el])
        self.assertEqual(out[0]["value"], "2020 01")

    def test_max_fields_cap(self):
        els = [FakeEl(f"标签{i}：值{i}", f"标签{i}：") for i in range(MAX_FIELDS + 5)]
        out = extract_fields(els)
        self.assertEqual(len(out), MAX_FIELDS)


if __name__ == "__main__":
    import unittest
    unittest.main()
