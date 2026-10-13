"""QT6 主窗口：左侧栏（域名/标签）+ 卡片流布局网格 + 工具栏 + 状态栏。

直接复用 almond.core，与 webui 共享存储/库。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from almond.core import records
from almond.core.proxy import list_proxy_domains

from .backend import DEFAULT_PROXY, FetchManager
from .card import CardWidget
from .dialogs import (
    AddUrlDialog,
    DetailDialog,
    ProxyPanel,
    RenameDialog,
    StoragePanel,
    TagDialog,
    TagManagerDialog,
)
from .flowlayout import FlowLayout


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Almond 看板 (QT)")
        self.resize(1120, 740)

        self.records: list[dict] = []
        self.proxied_domains: set[str] = set()
        self.cards: dict[str, CardWidget] = {}
        self.filter_domain: str | None = None
        self.filter_tag: int | None = None
        self.search = ""
        self.global_proxy = DEFAULT_PROXY

        self.mgr = FetchManager()
        self.mgr.set_global_proxy(self.global_proxy)
        self.mgr.fetched.connect(self._on_fetched)
        self.mgr.progress.connect(self._on_progress)
        self.mgr.finished.connect(self._on_fetch_finished)
        self.mgr.log.connect(self._on_log)

        self._build_ui()
        self.reload()
        # 打开即把从未抓取的记录自动补抓
        self.mgr.enqueue_pending()

    # ──────────────────────── UI 构建 ────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # 顶部工具栏
        top = QHBoxLayout()
        top.setContentsMargins(8, 8, 8, 4)
        for label, slot in (
            ("添加", self.show_add),
            ("补抓", lambda: self.mgr.enqueue_pending()),
            ("标签", self.show_tag_manager),
            ("代理", lambda: ProxyPanel(self).exec()),
            ("存储", lambda: StoragePanel(self).exec()),
            ("刷新", self.reload),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            top.addWidget(b)
        top.addStretch(1)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索标题 / URL…")
        self.search_edit.textChanged.connect(self._on_search)
        top.addWidget(self.search_edit, 2)
        root.addLayout(top)

        # 主体：侧栏 + 网格
        split = QSplitter(Qt.Orientation.Horizontal)
        self.sidebar = QListWidget()
        self.sidebar.itemClicked.connect(self._on_sidebar)
        split.addWidget(self.sidebar)

        right = QWidget()
        rbox = QVBoxLayout(right)
        rbox.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_widget = QWidget()
        self.flow = FlowLayout(self.grid_widget, margin=10, spacing=10)
        self.grid_widget.setLayout(self.flow)
        self.scroll.setWidget(self.grid_widget)
        rbox.addWidget(self.scroll)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        # 状态栏
        self.status_bar = QStatusBar()
        self.status_label = QLabel("")
        self.progress_label = QLabel("")
        self.status_bar.addPermanentWidget(self.progress_label)
        self.status_bar.addWidget(self.status_label)
        root.addWidget(self.status_bar)

    # ──────────────────────── 数据刷新 ────────────────────────
    def reload(self) -> None:
        self.records = records.list_records()
        self.proxied_domains = set(list_proxy_domains())
        self._refresh_sidebar()
        self._refresh_grid()
        self.status(f"共 {len(self.records)} 条记录")

    def _refresh_sidebar(self) -> None:
        self.sidebar.clear()
        all_item = QListWidgetItem("📚 全部")
        all_item.setData(Qt.ItemDataRole.UserRole, ("all", None))
        self.sidebar.addItem(all_item)

        counts: dict[str, int] = {}
        for r in self.records:
            d = r.get("domain")
            if d:
                counts[d] = counts.get(d, 0) + 1
        if counts:
            hdr = QListWidgetItem("— 域名 —")
            hdr.setFlags(Qt.ItemFlag.NoItemFlags)
            self.sidebar.addItem(hdr)
            for name in sorted(counts):
                proxied = name in self.proxied_domains
                label = f"{name} ({counts[name]})" + ("  🔒代理" if proxied else "")
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, ("domain", name))
                self.sidebar.addItem(it)

        tags = records.list_tags()
        if tags:
            hdr = QListWidgetItem("— 标签 —")
            hdr.setFlags(Qt.ItemFlag.NoItemFlags)
            self.sidebar.addItem(hdr)
            tcount: dict[int, int] = {}
            for r in self.records:
                for t in r.get("tags", []):
                    tcount[t["id"]] = tcount.get(t["id"], 0) + 1
            for t in tags:
                it = QListWidgetItem(f"{t['name']} ({tcount.get(t['id'], 0)})")
                it.setData(Qt.ItemDataRole.UserRole, ("tag", t["id"]))
                self.sidebar.addItem(it)

    def _visible_records(self) -> list[dict]:
        out = []
        s = self.search.lower()
        for r in self.records:
            if self.filter_domain and r.get("domain") != self.filter_domain:
                continue
            if self.filter_tag is not None:
                ids = {t["id"] for t in r.get("tags", [])}
                if self.filter_tag not in ids:
                    continue
            if s:
                if s not in (r.get("title") or "").lower() and s not in r.get("url", "").lower():
                    continue
            out.append(r)
        return out

    def _refresh_grid(self) -> None:
        while self.flow.count():
            item = self.flow.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.cards.clear()
        for rec in self._visible_records():
            card = CardWidget(rec, self.global_proxy, self)
            card.clicked.connect(self.open_detail)
            self.flow.addWidget(card)
            self.cards[rec["url"]] = card

    # ──────────────────────── 交互回调 ────────────────────────
    def _on_sidebar(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not payload:
            return
        kind, val = payload
        if kind == "all":
            self.filter_domain, self.filter_tag = None, None
        elif kind == "domain":
            self.filter_domain, self.filter_tag = val, None
        elif kind == "tag":
            self.filter_domain, self.filter_tag = None, val
        self._refresh_grid()

    def _on_search(self, text: str) -> None:
        self.search = text
        self._refresh_grid()

    def _rec_by_url(self, url: str) -> dict | None:
        return next((r for r in self.records if r["url"] == url), None)

    def open_detail(self, url: str) -> None:
        rec = self._rec_by_url(url)
        if rec:
            DetailDialog(rec, self.global_proxy, self).exec()

    def refetch(self, url: str) -> None:
        self.mgr.enqueue([url])

    def rename_record(self, url: str) -> None:
        rec = self._rec_by_url(url)
        if not rec:
            return
        d = RenameDialog(url, rec.get("title", ""), self)
        if d.exec() == QDialog.DialogCode.Accepted:
            new = d.value()
            if new:
                records.set_title(url, new)
                self.reload()

    def delete_record(self, url: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(self, "删除", "确定删除该记录？") == QMessageBox.StandardButton.Yes:
            records.delete_record(url)
            self.records = [r for r in self.records if r["url"] != url]
            self._refresh_grid()

    def tag_record(self, url: str) -> None:
        rec = self._rec_by_url(url)
        if not rec:
            return
        all_tags = records.list_tags()
        current = {t["id"] for t in rec.get("tags", [])}
        d = TagDialog(url, all_tags, current, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            chosen = d.chosen_ids()
            for t in all_tags:
                if t["id"] in chosen and t["id"] not in current:
                    records.add_tag_to_record(url, t["id"])
                elif t["id"] not in chosen and t["id"] in current:
                    records.remove_tag_from_record(url, t["id"])
            self.reload()

    def show_add(self) -> None:
        d = AddUrlDialog(self)
        if d.exec() == QDialog.DialogCode.Accepted:
            urls = d.urls()
            if urls:
                res = records.insert_quick_records([{"url": u} for u in urls])
                self.status(f"导入 {res['inserted']} 条，{res['skipped']} 条已存在")
                self.reload()
                self.mgr.enqueue(urls)

    def show_tag_manager(self) -> None:
        d = TagManagerDialog(self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.reload()

    def set_global_proxy(self, val: str) -> None:
        self.global_proxy = val
        self.mgr.set_global_proxy(val)

    # ──────────────────────── 抓取信号 ────────────────────────
    def _on_fetched(self, rec: dict) -> None:
        url = rec.get("url")
        idx = next((i for i, r in enumerate(self.records) if r["url"] == url), None)
        if idx is None:
            self.records.append(rec)
        else:
            self.records[idx] = rec
        card = self.cards.get(url)
        if card is not None:
            card.update_record(rec)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress_label.setText(f"补抓 {done}/{total}" if total else "")

    def _on_fetch_finished(self) -> None:
        self.status("补抓完成")

    def _on_log(self, msg: str) -> None:
        self.status(msg)

    def status(self, msg: str) -> None:
        self.status_label.setText(msg)
