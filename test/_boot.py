"""测试公共引导：隔离存储目录 + 统一 setUp/tearDown。

关键点：`almond.core.storage` 在 **import 时**读取 `ALMOND_DATA_DIR` 定死 `_BASE_DIR`
（config.json、默认数据目录都从它算）。所以必须在 import almond **之前**把
`ALMOND_DATA_DIR` 指向临时目录，避免把 config.json 写进仓库、避免碰到真实数据。

每个用例 setUp 再 `set_storage_dir(新临时目录)` 拿到一份全新空库；`enter_fetch()`
把线程本地的抓取纪元重新绑到当前纪元（清掉上个用例可能留下的陈旧绑定，否则
`fetch_hints.write_allowed()` 会误判为 False，让后续 hints 写入被静默跳过）。
"""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 必须在 import almond 之前设置（setdefault：尊重外部显式指定的 ALMOND_DATA_DIR）
os.environ.setdefault("ALMOND_DATA_DIR", tempfile.mkdtemp(prefix="almond_test_base_"))

from almond.core import fetch_hints, storage  # noqa: E402  环境就绪后再 import


class AlmondTestCase(unittest.TestCase):
    """每个用例一份独立临时存储目录 + 全新 SQLite 库。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="almond_case_")
        storage.set_storage_dir(self.dir)
        # 重新绑定抓取纪元到当前值：清掉上个用例的陈旧绑定，保证 write_allowed() 为 True；
        # 同时丢弃内存里的经验缓存（切库监听也会做，这里双保险）
        fetch_hints.enter_fetch()

    def tearDown(self):
        # 退出「效应收集」模式（有用例 enable_effects 后异常退出时不留全局污染）
        try:
            fetch_hints.take_effects()
        except Exception:
            pass
        shutil.rmtree(self.dir, ignore_errors=True)
