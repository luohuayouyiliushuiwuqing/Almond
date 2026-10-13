"""流式布局（FlowLayout）：宽度不足时自动换行，用于卡片网格。

移植自 Qt 官方示例，适配 PySide6。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidgetItem


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin: int = 0, spacing: int = -1):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing if spacing >= 0 else 6)
        self._items: list[QWidgetItem] = []

    def addItem(self, item: QWidgetItem) -> None:  # noqa: D102
        self._items.append(item)

    def count(self) -> int:  # noqa: D102
        return len(self._items)

    def itemAt(self, idx: int):  # noqa: D102
        return self._items[idx] if 0 <= idx < len(self._items) else None

    def takeAt(self, idx: int):  # noqa: D102
        return self._items.pop(idx) if 0 <= idx < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: D102
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: D102
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: D102
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: D102
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:  # noqa: D102
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: D102
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        space = self.spacing()
        for item in self._items:
            sz = item.sizeHint()
            w = sz.width()
            h = sz.height()
            if x + w > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y += line_height + space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), sz))
            x += w + space
            line_height = max(line_height, h)
        return y + line_height - rect.y() + m.top() + m.bottom()
