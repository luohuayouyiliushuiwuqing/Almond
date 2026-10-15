"""「标签: 值」行结构的字段解析（如 div.space-y-2 里的 发行日期/番号/标题…）。

看板（webui）使用：给定选择器匹配到的元素列表，
规则为行的第一个直接子元素是 <span>（标签），其后是纯文本 / <time> / <a>。

`extract_tags` 是另一种形态——**扁平标签链**（如 B 站视频页 .tag-panel .tag-link）：
每个匹配元素本身就是一个标签，没有「标签: 值」结构，取出文字 + 自身/后代链接即可。
"""

from urllib.parse import urljoin

MAX_FIELDS = 30
MAX_TAGS = 60


def _collapse(text: str) -> str:
    return " ".join((text or "").split())


def extract_fields(elements) -> list[dict]:
    fields = []
    for el in list(elements)[:MAX_FIELDS]:
        children = list(getattr(el, "children", None) or [])
        if not children or getattr(children[0], "tag", None) != "span":
            continue
        label_full = _collapse(children[0].get_all_text())
        label = label_full.rstrip("：: ").strip()
        full = _collapse(el.get_all_text())
        value = full[len(label_full):] if full.startswith(label_full) else full.replace(label_full, "", 1)
        value = value.strip().lstrip(",，").strip()

        times = el.css("time")
        datetime_val = times[0].attrib.get("datetime", "") if times else ""
        links = [{"text": _collapse(a.get_all_text()), "href": a.attrib.get("href", "")} for a in el.css("a")]
        # value 原文包含链接文字，去掉重复部分，只留链接之外的剩余文字
        for l in links:
            if l["text"]:
                value = value.replace(l["text"], "", 1)
        value = _collapse(value).strip(" ,，、;；")
        if not label or (not value and not links and not datetime_val):
            continue
        fields.append({"label": label, "value": value, "links": links, "datetime": datetime_val})
    return fields


def extract_tags(elements, base_url: str = "") -> list[dict]:
    """扁平标签链提取（`detail_mode="tags"`）：每个匹配元素本身就是一个标签。

    与 extract_fields 的「标签: 值」行不同，这里没有 label——产物统一
    `{label:"", value:文字, links:[{text,href}], datetime}`，前端把连续的无 label
    项渲染成一行标签芯片。链接取两处：**元素自身**的 href（`<a class="tag-link">`
    自己就是链）与后代里的 `<a>`（嵌套结构如 `<a><span>文字</span></a>`），
    同 href 去重；协议相对的 `//host/…` 用 base_url 补成绝对地址。
    """
    tags: list[dict] = []
    for el in list(elements)[:MAX_TAGS]:
        text = _collapse(el.get_all_text())
        if not text:
            continue
        links: list[dict] = []
        seen: set[str] = set()
        own_href = el.attrib.get("href", "")
        if own_href:
            href = urljoin(base_url, own_href)
            seen.add(href)
            links.append({"text": text, "href": href})
        for a in el.css("a"):
            href = a.attrib.get("href", "")
            if not href:
                continue
            href = urljoin(base_url, href)
            if href in seen:
                continue
            seen.add(href)
            links.append({"text": _collapse(a.get_all_text()), "href": href})
        times = el.css("time")
        datetime_val = times[0].attrib.get("datetime", "") if times else ""
        tags.append({"label": "", "value": text, "links": links, "datetime": datetime_val})
    return tags
