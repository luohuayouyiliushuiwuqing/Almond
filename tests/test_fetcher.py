"""fetcher 纯逻辑：_sid 编码 / extract_metadata / get_metadata 封面规则。

真实抓取走网络 + 子进程，不在单元测试范围——这里只测**不触网**的部分：
get_metadata 通过打桩 fetch_page 注入假 Response。
"""

import unittest.mock as mock

import _boot  # noqa: F401
from almond.core import fetcher


class FakeNode:
    def __init__(self, text):
        self.text = text


class FakeSel:
    """模拟 scrapling 的 css() 结果：可 extract_first / 迭代 / 下标。"""

    def __init__(self, first=None, title_text=None):
        self._first = first
        self._list = [FakeNode(title_text)] if title_text is not None else []

    def extract_first(self):
        return self._first

    def __getitem__(self, i):
        return self._list[i]

    def __len__(self):
        return len(self._list)

    def __iter__(self):
        return iter(self._list)


class FakeResponse:
    def __init__(self, attrs=None, title_text=None, url="https://ex.com/page"):
        self._attrs = attrs or {}
        self._title = title_text
        self.url = url

    def css(self, selector):
        return FakeSel(self._attrs.get(selector),
                       self._title if selector == "title" else None)


class TestSid(_boot.AlmondTestCase):
    def test_encode(self):
        self.assertEqual(fetcher._sid("direct", "static"), "direct|static")
        self.assertEqual(fetcher._sid("proxy", "static", {"impersonate": "firefox147"}),
                         "proxy|static|impersonate=firefox147")
        self.assertEqual(fetcher._sid("stealth", "stealth", {"doh": True}),
                         "stealth|stealth|doh")            # True → 只留键


class TestExtractMetadata(_boot.AlmondTestCase):
    def test_og_priority_and_urljoin(self):
        resp = FakeResponse(attrs={
            'meta[property="og:title"]::attr(content)': "OG Title",
            'meta[property="og:image"]::attr(content)': "/img/a.png",
            'link[rel*="icon"]::attr(href)': "/favicon.ico",
        })
        m = fetcher.extract_metadata(resp, "https://ex.com/page")
        self.assertEqual(m["title"], "OG Title")
        self.assertEqual(m["thumbnail"], "https://ex.com/img/a.png")
        self.assertEqual(m["favicon"], "https://ex.com/favicon.ico")

    def test_twitter_then_title_fallback(self):
        resp = FakeResponse(attrs={'meta[name="twitter:title"]::attr(content)': "TW"})
        self.assertEqual(fetcher.extract_metadata(resp, "https://ex.com/")["title"], "TW")
        resp2 = FakeResponse(title_text="  Doc Title  ")
        self.assertEqual(fetcher.extract_metadata(resp2, "https://ex.com/")["title"], "Doc Title")

    def test_absolute_image_unchanged(self):
        resp = FakeResponse(attrs={'meta[property="og:image"]::attr(content)':
                                   "https://cdn.com/x.png"})
        self.assertEqual(fetcher.extract_metadata(resp, "https://ex.com/")["thumbnail"],
                         "https://cdn.com/x.png")


class TestGetMetadataCover(_boot.AlmondTestCase):
    def _resp(self):
        return FakeResponse(attrs={
            'meta[property="og:title"]::attr(content)': "T",
            'meta[property="og:image"]::attr(content)': "/cover.png",
            'link[rel*="icon"]::attr(href)': "/favicon.ico",
        })

    def _run(self, cover):
        with mock.patch.object(fetcher, "fetch_page", return_value=self._resp()):
            return fetcher.get_metadata("https://ex.com/page", cover=cover)

    def test_cover_none_uses_og_image(self):
        self.assertEqual(self._run(None)["thumbnail"], "https://ex.com/cover.png")

    def test_cover_false_drops_thumbnail(self):
        self.assertEqual(self._run(False)["thumbnail"], "")

    def test_cover_true_uses_favicon(self):
        self.assertEqual(self._run(True)["thumbnail"], "https://ex.com/favicon.ico")

    def test_success_and_details(self):
        m = self._run(None)
        self.assertTrue(m["success"])
        self.assertEqual(m["details"], [])                 # 假 Response 的详情选择器返回空


if __name__ == "__main__":
    import unittest
    unittest.main()
