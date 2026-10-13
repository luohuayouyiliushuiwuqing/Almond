"""QT6 界面入口（almond-webui-qt）。

原生 PySide6 控件，直接复用 almond.core，与 webui 共享存储/库。
"""

from __future__ import annotations

import sys


def main(argv=None) -> int:  # noqa: ARG001
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
    except ImportError:
        sys.stderr.write(
            "Almond 的 QT 界面需要 PySide6，请先安装：\n"
            "  pip install PySide6\n"
            "（可参考仓库根的 requirements-qt.txt）\n")
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("Almond")
    app.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    try:
        from .main_window import MainWindow
    except ImportError as exc:  # PySide6 缺失时给出友好提示
        sys.stderr.write(f"无法加载 QT 界面：{exc}\n请确认 PySide6 已正确安装。\n")
        return 1

    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
