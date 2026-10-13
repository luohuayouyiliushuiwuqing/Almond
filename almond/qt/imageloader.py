"""缩略图/图标加载：按 URL 走代理解析（国内旁路），结果回填 QPixmap。

用 QThreadPool + QRunnable，并发受全局线程池上限约束（默认 8），避免千级卡片
各开一个线程。
"""

from __future__ import annotations

import urllib.request
from urllib.request import ProxyHandler, build_opener

from PySide6.QtCore import QByteArray, QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QPixmap

from almond.core import proxy as proxy_mod


class _ImgSignals(QObject):
    done = Signal(QPixmap)


def _download(url: str, global_proxy: str) -> bytes:
    proxy = proxy_mod.resolve_proxy(url, global_proxy) if global_proxy else None
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
    )
    if proxy:
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
    else:
        opener = build_opener()
    with opener.open(req, timeout=12) as resp:
        return resp.read(5_000_000)  # 单图上限 5MB


class ImageTask(QRunnable):
    def __init__(self, url: str, global_proxy: str = ""):
        super().__init__()
        # 禁止自动删除：否则线程池会在 run() 返回后立即销毁本对象，
        # 而其 signals QObject 可能先于跨线程排队信号的投递被析构，
        # 触发 "Signal source has been deleted"。由持有方（卡片/对话框）保留引用。
        self.setAutoDelete(False)
        self.url = url
        self.global_proxy = global_proxy or ""
        self.signals = _ImgSignals()

    def run(self) -> None:
        pix = QPixmap()
        try:
            data = _download(self.url, self.global_proxy)
            pix.loadFromData(QByteArray(data))
        except Exception:
            pass
        self.signals.done.emit(pix)
