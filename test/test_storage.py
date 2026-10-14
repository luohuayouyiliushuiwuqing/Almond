"""storage：存储目录切换 / 纪元 / 历史 / 回调（全程临时目录，不碰仓库 config.json）。"""

import _boot  # noqa: F401
from almond.core import storage


class TestStorage(_boot.AlmondTestCase):
    def test_set_dir_points_db_under_it(self):
        info = storage.get_storage_paths()
        self.assertEqual(info["path"], str(storage.data_dir()))
        self.assertEqual(info["db_path"], str(storage.data_dir() / "metadata.db"))

    def test_switch_increments_epoch(self):
        before = storage.storage_epoch()
        import tempfile
        storage.set_storage_dir(tempfile.mkdtemp(prefix="almond_sw_"))
        self.assertEqual(storage.storage_epoch(), before + 1)

    def test_same_dir_is_noop(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="almond_sw_")
        storage.set_storage_dir(d)
        e = storage.storage_epoch()
        info = storage.set_storage_dir(d)          # 同路径重复保存
        self.assertEqual(storage.storage_epoch(), e)
        self.assertIn("db_used_existing", info)

    def test_existing_db_detected(self):
        import os
        import tempfile
        d = tempfile.mkdtemp(prefix="almond_sw_")
        (open(os.path.join(d, "metadata.db"), "wb")).close()
        info = storage.set_storage_dir(d)
        self.assertTrue(info["db_used_existing"])

    def test_reject_empty(self):
        with self.assertRaises(ValueError):
            storage.set_storage_dir("   ")

    def test_reject_file_path(self):
        import os
        import tempfile
        d = tempfile.mkdtemp(prefix="almond_sw_")
        f = os.path.join(d, "afile")
        open(f, "w").close()
        with self.assertRaises(ValueError):
            storage.set_storage_dir(f)

    def test_listener_fires_on_switch(self):
        import tempfile
        seen = []
        storage.on_storage_changed(lambda: seen.append(1))
        storage.set_storage_dir(tempfile.mkdtemp(prefix="almond_sw_"))
        self.assertEqual(len(seen), 1)

    def test_history_has_current_on_top(self):
        hist = storage._storage_history(str(storage.data_dir()))
        self.assertEqual(hist[0], str(storage.data_dir()))


if __name__ == "__main__":
    import unittest
    unittest.main()
