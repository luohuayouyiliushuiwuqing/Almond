"""Almond 的 QT6 原生界面（PySide6）。

与 webui 并存：直接复用 almond.core，共享同一存储目录与 metadata.db，
不依赖 web 服务。抓取为后台线程同步调用 core，结果经 records.upsert_record 入库。
"""
