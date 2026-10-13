"""QT UI 的对话框集合：添加 / 重命名 / 详情 / 标签 / 标签管理 / 代理面板 / 存储面板。"""

from __future__ import annotations

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QPixmap, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from almond.core import domains, proxy as proxy_mod, records, storage

from .imageloader import ImageTask, QThreadPool


class AddUrlDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加链接")
        self.resize(440, 280)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("每行一个 URL，可粘贴多个："))
        self.edit = QPlainTextEdit()
        layout.addWidget(self.edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def urls(self) -> list[str]:
        return [u.strip() for u in self.edit.toPlainText().splitlines() if u.strip()]


class RenameDialog(QDialog):
    def __init__(self, url: str, current: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("重命名")
        layout = QFormLayout(self)
        layout.addRow("URL", QLabel(url))
        self.edit = QLineEdit(current)
        layout.addRow("标题", self.edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def value(self) -> str:
        return self.edit.text().strip()


class DetailDialog(QDialog):
    def __init__(self, rec: dict, global_proxy: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(rec.get("title") or rec.get("url"))
        self.resize(540, 500)
        layout = QVBoxLayout(self)

        thumb = QLabel()
        thumb.setFixedHeight(200)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet("background:#1e1e1e; border-radius:4px;")
        if rec.get("thumbnail"):
            task = ImageTask(rec["thumbnail"], global_proxy)
            self._task = task  # 保留引用，避免信号源被提前析构
            task.signals.done.connect(
                lambda p: (
                    thumb.setPixmap(p.scaledToHeight(200, Qt.TransformationMode.SmoothTransformation)),
                    thumb.setText(""),
                ) if not p.isNull() else None)
            QThreadPool.globalInstance().start(task)
            thumb.setText("加载中…")
        else:
            thumb.setText("无缩略图")
        layout.addWidget(thumb)

        layout.addWidget(QLabel(f"URL：{rec.get('url')}"))
        details = rec.get("details") or []
        if details:
            for d in details:
                layout.addWidget(QLabel(f"{d.get('label', '')}：{d.get('value', '')}"))
        else:
            layout.addWidget(QLabel("（无详情字段）"))
        tags = ",".join(t.get("name", "") for t in rec.get("tags", [])) or "（无标签）"
        layout.addWidget(QLabel(f"标签：{tags}"))
        btn = QPushButton("关闭")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)


class TagDialog(QDialog):
    """给某条记录勾选标签。"""

    def __init__(self, url: str, all_tags: list[dict], current_ids: set, parent=None):
        super().__init__(parent)
        self.setWindowTitle("标签")
        self.all_tags = all_tags
        self.current_ids = set(current_ids)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(url))
        self.list = QListWidget()
        for t in all_tags:
            item = QListWidgetItem(t["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if t["id"] in self.current_ids
                else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, t["id"])
            self.list.addItem(item)
        layout.addWidget(self.list)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def chosen_ids(self) -> set[int]:
        out: set[int] = set()
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.add(item.data(Qt.ItemDataRole.UserRole))
        return out


class TagManagerDialog(QDialog):
    """全局标签管理：新建 / 删除。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("标签管理")
        self.resize(360, 360)
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self._load()
        layout.addWidget(self.list)

        add_row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("新标签名")
        add_btn = QPushButton("新建")
        add_row.addWidget(self.edit)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        del_btn = QPushButton("删除选中")
        layout.addWidget(del_btn)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        add_btn.clicked.connect(self._add)
        del_btn.clicked.connect(self._del)

    def _load(self) -> None:
        self.list.clear()
        for t in records.list_tags():
            self.list.addItem(t["name"])

    def _add(self) -> None:
        name = self.edit.text().strip()
        if name:
            records.create_tag(name)
            self.edit.clear()
            self._load()

    def _del(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        name = item.text()
        tag = next((t for t in records.list_tags() if t["name"] == name), None)
        if tag and QMessageBox.question(
                self, "删除", f"删除标签「{name}」？") == QMessageBox.StandardButton.Yes:
            records.delete_tag(tag["id"])
            self._load()


class ProxyPanel(QDialog):
    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.setWindowTitle("代理")
        self.resize(580, 520)
        layout = QVBoxLayout(self)

        gl = QFormLayout()
        self.proxy_edit = QLineEdit(mw.global_proxy)
        gl.addRow("全局代理地址", self.proxy_edit)
        layout.addLayout(gl)

        h = QHBoxLayout()
        detect = QPushButton("探测本机端口(7889-7899)")
        save = QPushButton("保存")
        h.addWidget(detect)
        h.addWidget(save)
        layout.addLayout(h)
        detect.clicked.connect(self._detect)
        save.clicked.connect(self._save)

        layout.addWidget(QLabel("URL 通配符规则（如 *.google.com / github.com/*）"))
        self.rules_list = QListWidget()
        layout.addWidget(self.rules_list)
        rh = QHBoxLayout()
        self.rule_edit = QLineEdit()
        self.rule_edit.setPlaceholderText("模式")
        self.rule_need = QComboBox()
        self.rule_need.addItems(["用代理", "强制直连"])
        add_rule = QPushButton("添加")
        del_rule = QPushButton("删除")
        rh.addWidget(self.rule_edit)
        rh.addWidget(self.rule_need)
        rh.addWidget(add_rule)
        rh.addWidget(del_rule)
        layout.addLayout(rh)
        add_rule.clicked.connect(self._add_rule)
        del_rule.clicked.connect(self._del_rule)

        layout.addWidget(QLabel("域名路由（need_proxy 三态）"))
        self.dom_table = QTableWidget()
        self.dom_table.setColumnCount(2)
        self.dom_table.setHorizontalHeaderLabels(["域名", "代理"])
        self.dom_table.verticalHeader().setVisible(False)
        layout.addWidget(self.dom_table)

        self._load()

    def _load(self) -> None:
        self.rules_list.clear()
        for r in records.list_proxy_rules():
            item = QListWidgetItem(
                f"{r['pattern']} → {'用代理' if r['need_proxy'] else '直连'}")
            item.setData(Qt.ItemDataRole.UserRole, r["id"])
            self.rules_list.addItem(item)

        doms = domains.list_domains()
        self.dom_table.setRowCount(len(doms))
        for i, d in enumerate(doms):
            self.dom_table.setItem(i, 0, QTableWidgetItem(d["name"]))
            cb = QComboBox()
            cb.addItems(["跟随全局", "用代理", "强制直连"])
            need = d["need_proxy"]
            cb.setCurrentIndex(0 if need is None else (1 if need else 2))
            cb.currentIndexChanged.connect(
                lambda idx, name=d["name"]: self._domain_set(name, idx))
            self.dom_table.setCellWidget(i, 1, cb)

    def _detect(self) -> None:
        found = proxy_mod.detect_working_proxy()
        if found:
            self.proxy_edit.setText(found)
            QMessageBox.information(self, "探测", f"找到可用本机代理：{found}")
        else:
            QMessageBox.information(self, "探测", "未发现可用的本机代理端口")

    def _save(self) -> None:
        self.mw.set_global_proxy(self.proxy_edit.text().strip())
        QMessageBox.information(self, "保存", "全局代理已更新，下一条抓取起生效")

    def _add_rule(self) -> None:
        pat = self.rule_edit.text().strip()
        if not pat:
            return
        need = self.rule_need.currentIndex() == 0
        records.upsert_proxy_rule(pat, need)
        self.rule_edit.clear()
        self._load()

    def _del_rule(self) -> None:
        item = self.rules_list.currentItem()
        if item is None:
            return
        rid = item.data(Qt.ItemDataRole.UserRole)
        records.delete_proxy_rule(rid)
        self._load()

    @staticmethod
    def _domain_set(name: str, idx: int) -> None:
        value = {0: None, 1: True, 2: False}[idx]
        domains.update_domain(name, need_proxy=value)


class StoragePanel(QDialog):
    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.setWindowTitle("存储")
        self.resize(520, 420)
        layout = QVBoxLayout(self)

        info = storage.get_storage_paths()
        layout.addWidget(QLabel(f"当前目录：{info['path']}"))
        layout.addWidget(QLabel(f"数据库：{info['db_path']}"))
        layout.addWidget(QLabel(f"图片缓存：{info['cache_dir']}"))
        layout.addWidget(QLabel(f"存储纪元：{info['epoch']}"))

        hl = QHBoxLayout()
        self.path_edit = QLineEdit(info["path"])
        browse = QPushButton("浏览…")
        switch = QPushButton("切换")
        hl.addWidget(QLabel("新目录："))
        hl.addWidget(self.path_edit)
        hl.addWidget(browse)
        hl.addWidget(switch)
        layout.addLayout(hl)
        browse.clicked.connect(self._browse)
        switch.clicked.connect(self._switch)

        layout.addWidget(QLabel("历史位置（点击切换）："))
        self.hist = QListWidget()
        for p in info["history"]:
            self.hist.addItem(p)
        self.hist.itemDoubleClicked.connect(self._switch_history)
        layout.addWidget(self.hist)

        note = QLabel("注意：切换不迁移数据，旧目录保留原样、新目录从零开始。")
        note.setStyleSheet("color:#a85;")
        layout.addWidget(note)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择存储目录", self.path_edit.text())
        if d:
            self.path_edit.setText(d)

    def _switch(self) -> None:
        path = self.path_edit.text().strip()
        if not path:
            return
        try:
            storage.set_storage_dir(path)
        except ValueError as exc:
            QMessageBox.warning(self, "切换失败", str(exc))
            return
        self.mw.reload()
        QMessageBox.information(self, "切换", f"已切换存储目录：{path}")
        self.accept()

    def _switch_history(self, item: QListWidgetItem) -> None:
        self.path_edit.setText(item.text())
        self._switch()
