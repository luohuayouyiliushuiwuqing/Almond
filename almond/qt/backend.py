"""QT UI 的后端桥接：直接复用 almond.core，与 webui 共享存储/库。

抓取在后台线程里同步调用 almond.core.fetcher.get_metadata（与 demo server 同款
in-process 直连写法），结果经 records.upsert_record 合并入库。
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QObject, Signal, QThread, QMutex, QMutexLocker

from almond.core import records, domains, storage, proxy as proxy_mod
from almond.core.fetcher import get_metadata

# 默认全局代理地址（与 README「说明」一节一致，落在本机 7889-7899 探测区间）
DEFAULT_PROXY = "http://127.0.0.1:7897"


class FetchManager(QObject):
    """后台抓取池：维护一个 URL 队列，最多 max_workers 个 QThread 并发抓取。"""

    fetched = Signal(dict)          # 入库后的记录 dict（含 tags/domain/details）
    progress = Signal(int, int)     # (done, total)
    finished = Signal()
    log = Signal(str)

    def __init__(self, parent: QObject | None = None, max_workers: int = 5):
        super().__init__(parent)
        self.max_workers = max_workers
        self.global_proxy = DEFAULT_PROXY
        self._queue: list[str] = []
        self._lock = QMutex()
        self._total = 0
        self._done = 0
        self._finished_emitted = False
        self._workers: list["_FetchWorker"] = []

    # ---- 配置 ----
    def set_global_proxy(self, proxy: str | None) -> None:
        self.global_proxy = proxy or ""

    # ---- 入队 ----
    def enqueue(self, urls: Iterable[str], explicit: bool = False) -> None:
        cleaned = [u.strip() for u in urls if u and u.strip()]
        if not cleaned:
            return
        with QMutexLocker(self._lock):
            self._queue.extend(cleaned)
            self._total += len(cleaned)
            self._finished_emitted = False
        self.progress.emit(self._done, self._total)
        self._spawn()

    def enqueue_pending(self) -> None:
        """补抓所有从未抓取过的记录（fetched=0）。"""
        self.enqueue(records.list_pending_urls())

    # ---- 供 worker 调用 ----
    def _spawn(self) -> None:
        with QMutexLocker(self._lock):
            running = sum(1 for w in self._workers if w.isRunning())
            idle = len(self._queue)
            to_start = max(0, min(self.max_workers - running, idle))
            for _ in range(to_start):
                w = _FetchWorker(self)
                self._workers.append(w)
                w.start()

    def _pop(self) -> str | None:
        with QMutexLocker(self._lock):
            if not self._queue:
                return None
            return self._queue.pop(0)

    def _on_done(self, meta: dict) -> None:
        with QMutexLocker(self._lock):
            self._done += 1
            done, total, still = self._done, self._total, len(self._queue)
            finished = (not self._finished_emitted) and still == 0 and done >= total and total > 0
            if finished:
                self._finished_emitted = True
        self.fetched.emit(meta)
        self.progress.emit(done, total)
        if finished:
            self.finished.emit()


class _FetchWorker(QThread):
    def __init__(self, mgr: FetchManager):
        super().__init__(mgr)
        self.mgr = mgr

    def run(self) -> None:
        while True:
            url = self.mgr._pop()
            if url is None:
                break
            try:
                meta = self._fetch_one(url)
            except Exception as exc:  # noqa: BLE001
                self.mgr.log.emit(f"抓取异常 {url}：{exc}")
                meta = {"url": url, "title": "", "thumbnail": "", "favicon": "",
                        "success": False, "details": []}
            try:
                stored = records.upsert_record(meta)
            except Exception as exc:  # noqa: BLE001
                self.mgr.log.emit(f"入库失败 {url}：{exc}")
                stored = meta
            self.mgr._on_done(stored)

    def _fetch_one(self, url: str) -> dict:
        cfg = domains.get_domain_fetch_config(url)
        selector = cfg.get("detail_selector") or None
        cover = cfg.get("cover")
        route = proxy_mod.forced_route(url)
        proxy = proxy_mod.resolve_proxy(url, self.mgr.global_proxy)
        self.mgr.log.emit(f"抓取中 {url}（代理：{proxy or '直连'}，路由：{route or '默认'}）")
        meta = get_metadata(url, timeout=20, proxy=proxy, selector=selector,
                            cover=cover, force_route=route)
        return meta
