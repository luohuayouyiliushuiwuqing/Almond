"""db：连接 + schema + 迁移幂等（WAL、五张表、可读写）。"""

import _boot  # noqa: F401
from almond.core import db, storage


class TestDb(_boot.AlmondTestCase):
    def test_schema_tables_created(self):
        with db.conn() as conn:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("records", "domains", "tags", "record_tags", "proxy_rules"):
            self.assertIn(t, names)

    def test_wal_mode(self):
        with db.conn() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_ensure_idempotent_open_twice(self):
        # _ensure 每连接都跑；重复开不能报 duplicate column
        with db.conn() as conn:
            conn.execute(
                "INSERT INTO records (url, title) VALUES ('https://a.com/', 'A')")
        with db.conn() as conn:
            row = conn.execute("SELECT title FROM records WHERE url='https://a.com/'").fetchone()
        self.assertEqual(row["title"], "A")

    def test_conn_creates_missing_dir(self):
        import shutil
        import tempfile
        d = tempfile.mkdtemp(prefix="almond_dbdir_")
        try:
            shutil.rmtree(d)                      # 删掉目录
            storage.set_storage_dir(d)            # set_storage_dir 会重建目录
            self.assertTrue(storage.db_path().parent.is_dir())
            with db.conn() as conn:
                conn.execute("SELECT count(*) FROM records").fetchone()
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    import unittest
    unittest.main()
