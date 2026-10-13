"""卡片控件：缩略图 + 标题 + 状态点 + 标签，点击看详情，右键出操作菜单。"""

from __future__ import annotations

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QVBoxLayout,
)

from .imageloader import ImageTask, QThreadPool

THUMB_W = 200
THUMB_H = 120


class CardWidget(QFrame):
    clicked = Signal(str)

    def __init__(self, rec: dict, global_proxy: str = "", mw=None):
        super().__init__()
        self.rec = rec
        self.url = rec.get("url", "")
        self.global_proxy = global_proxy
        self.mw = mw
        self._task = None
        self._init_ui()

    def _init_ui(self) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(THUMB_W + 14)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)

        self.thumb = QLabel()
        self.thumb.setFixedSize(THUMB_W, THUMB_H)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet("background:#1e1e1e; color:#777; border-radius:4px;")
        self.thumb.setText("无缩略图")
        layout.addWidget(self.thumb)

        self.title = QLabel(self.rec.get("title") or self.url)
        self.title.setWordWrap(True)
        self.title.setFixedHeight(36)
        self.title.setStyleSheet("font-size:13px; color:#ddd;")
        layout.addWidget(self.title)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.dot = QLabel("●")
        self._set_dot(bool(self.rec.get("success")))
        row.addWidget(self.dot)
        self.tag_label = QLabel(self._tags_text())
        self.tag_label.setStyleSheet("color:#6cf; font-size:11px;")
        self.tag_label.setMaximumWidth(150)
        row.addWidget(self.tag_label)
        layout.addLayout(row)

        if self.rec.get("thumbnail"):
            self._load_thumb(self.rec["thumbnail"])

    def _tags_text(self) -> str:
        return ",".join(t.get("name", "") for t in self.rec.get("tags", []))

    def _set_dot(self, ok: bool) -> None:
        self.dot.setStyleSheet(f"color:{'#3c3' if ok else '#c33'};")

    def _load_thumb(self, url: str) -> None:
        self._task = ImageTask(url, self.global_proxy)
        self._task.signals.done.connect(self._on_thumb)
        QThreadPool.globalInstance().start(self._task)

    def _on_thumb(self, pix: QPixmap) -> None:
        if not pix.isNull():
            self.thumb.setPixmap(
                pix.scaled(THUMB_W, THUMB_H, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation))
            self.thumb.setText("")
        else:
            self.thumb.setText("图加载失败")

    def update_record(self, rec: dict) -> None:
        self.rec = rec
        self.title.setText(rec.get("title") or rec.get("url"))
        self._set_dot(bool(rec.get("success")))
        self.tag_label.setText(self._tags_text())
        self.url = rec.get("url", "")

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        self.clicked.emit(self.url)
        super().mousePressEvent(ev)

    def contextMenuEvent(self, ev) -> None:  # noqa: N802
        if self.mw is None:
            return
        menu = QMenu(self)
        a_detail = menu.addAction("查看详情")
        a_refetch = menu.addAction("重新抓取")
        a_rename = menu.addAction("重命名")
        a_tag = menu.addAction("标签")
        a_open = menu.addAction("浏览器打开")
        a_del = menu.addAction("删除")
        act = menu.exec(ev.globalPos())
        if act is None:
            return
        if act == a_detail:
            self.mw.open_detail(self.url)
        elif act == a_refetch:
            self.mw.refetch(self.url)
        elif act == a_rename:
            self.mw.rename_record(self.url)
        elif act == a_tag:
            self.mw.tag_record(self.url)
        elif act == a_open:
            QDesktopServices.openUrl(QUrl(self.url))
        elif act == a_del:
            self.mw.delete_record(self.url)
