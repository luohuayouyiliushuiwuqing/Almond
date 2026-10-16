#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把书签文件恢复到「只有链接」的初始状态：剥掉标题、标签、时间戳、封面，
逐行输出 URL（.txt），可以直接再喂给看板的「导入」。

用法（在仓库根目录执行）：
    python strip_to_links.py bookmarks.html
    python strip_to_links.py bookmarks.html -o links.txt
    python strip_to_links.py bookmarks.html --stdout
    python strip_to_links.py bookmarks.txt --no-dedupe
    python strip_to_links.py 收藏.html --absolute https://example.com/base.html

输入形态自动识别（不看扩展名）：内容里有 <A HREF> 就当书签 HTML 解析，否则按 TXT
（一行一个 URL，`#` 开头与空行忽略）。

链接的取值规则与看板导入（frontend/src/Toolbar.jsx 的 onImport）一致：
  • 只保留 http/https，丢掉 javascript:/file:/mailto:/data: 这类；
  • HTML 里先按 base 把相对链接补全（浏览器 DOMParser 会做这件事，这里对齐）；
  • 去重时**参数原样保留**——导入时去的是「带不带尾斜杠」，
    `https://a.com/x/` 与 `https://a.com/x` 是两个条目，别在这里替它合并。

仅用标准库。默认输出写在输入文件旁边：<同目录>/<同名>_links.txt。
"""
import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

# 有 <A HREF=...> 就当书签 HTML；只看标记本身，标题文本里出现 "ahref" 不算
_HTML_A = re.compile(r"<a\s[^>]*href\s*=", re.IGNORECASE)


class LinkCollector(HTMLParser):
    """收集 <a href> 的原始值（HTML 实体由 stdlib 解析器自动转义还原）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value.strip())
                break


def is_bookmark_html(text: str) -> bool:
    return bool(_HTML_A.search(text))


def links_from_html(text: str, base: str | None) -> list[str]:
    parser = LinkCollector()
    parser.feed(text)
    parser.close()
    out = []
    for href in parser.hrefs:
        out.append(urljoin(base, href) if base else href)
    return out


def links_from_text(text: str) -> list[str]:
    """TXT：一行一个 URL；`#` 注释与空行跳过。不替用户改写 URL。"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def only_http(urls: list[str]) -> tuple[list[str], list[str]]:
    keep, dropped = [], []
    for u in urls:
        (keep if urlparse(u).scheme.lower() in ("http", "https") else dropped).append(u)
    return keep, dropped


def dedupe(urls: list[str]) -> tuple[list[str], int]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out, len(urls) - len(out)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="书签文件 → 只保留链接（逐行 URL）",
    )
    ap.add_argument("file", help="要还原的书签文件（HTML 或 TXT，形态自动识别）")
    ap.add_argument("-o", "--output", help="输出路径（默认 <输入名>_links.txt，写在同目录）")
    ap.add_argument("--stdout", action="store_true", help="只打到标准输出，不落盘")
    ap.add_argument("--no-dedupe", action="store_true", help="保留重复条目（默认去重）")
    ap.add_argument("--absolute", metavar="URL",
                    help="按这个页面地址补全 HTML 里的相对链接（如导出时记录的原始页面）")
    args = ap.parse_args()

    src = Path(args.file).expanduser()
    if not src.is_file():
        sys.exit(f"[X] 文件不存在：{src}")

    # utf-8-sig：Windows 记事本/PowerShell 写出的带 BOM 文件也能读
    try:
        text = src.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = src.read_text(encoding="utf-8-sig", errors="replace")
        print("[!] 文件不是合法 UTF-8，已按替换字符读入", file=sys.stderr)

    if is_bookmark_html(text):
        kind = "HTML"
        urls = links_from_html(text, args.absolute)
    else:
        kind = "TXT"
        urls = links_from_text(text)

    total_in = len(urls)
    urls, dropped = only_http(urls)
    dupes = 0
    if not args.no_dedupe:
        urls, dupes = dedupe(urls)

    if not urls:
        print(f"[!] 没找到 http(s) 链接（读到 {total_in} 条，"
              f"非 http(s) 丢掉 {len(dropped)} 条）。", file=sys.stderr)
        return

    body = "\n".join(urls) + "\n"
    if args.stdout:
        sys.stdout.write(body)
    else:
        out = Path(args.output).expanduser() if args.output else src.with_name(
            f"{src.stem}_links.txt")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        print(f"[*] 输入：{src}（按 {kind} 解析，共 {total_in} 条链接标记）")
        if dropped:
            sample = "、".join(dropped[:3])
            print(f"[*] 丢掉非 http(s)：{len(dropped)} 条（如 {sample}）")
        if dupes:
            print(f"[*] 去重：{dupes} 条")
        print(f"[*] 已写出：{out.resolve()}  共 {len(urls)} 行")


if __name__ == "__main__":
    main()
