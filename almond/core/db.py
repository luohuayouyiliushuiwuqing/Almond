"""SQLite 基建：连接 + schema + 迁移（`_ensure` 每连接幂等执行）。

业务读写分属两处：records.py（记录 / 标签 / 代理规则 CRUD）、
domains.py（域名配置 / 域名重置）。本模块只管「库怎么开、表怎么建」。
"""

import sqlite3
import threading
from contextlib import contextmanager

from almond.core import storage
from almond.core.hostutil import root_domain

# _ensure 的建表/迁移是「先查后改」——两条并发连接同时首开新库会双双 ALTER，
# 第二条报 duplicate column。进程内一把锁把 check-then-act 做成原子
# （子进程不写库，跨进程无此问题）。
_ENSURE_LOCK = threading.Lock()

# domains 表的老库回填只按库跑一次（见 _ensure 里的用法）。
# 记**路径集合**：切到新库会自动跑一次，但切回曾经打开过的库不再重复 O(N) 回填
# ——在两个目录之间来回切换时，每次白付一遍全表扫正是「切换慢」的一份来源。
_DOMAINS_BACKFILLED_FOR: set[str] = set()

_SCHEMA_RECORDS = """
CREATE TABLE IF NOT EXISTS records (
    url        TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    thumbnail  TEXT NOT NULL DEFAULT '',
    favicon    TEXT NOT NULL DEFAULT '',
    success    INTEGER NOT NULL DEFAULT 0,
    details    TEXT NOT NULL DEFAULT '[]',
    fetched    INTEGER NOT NULL DEFAULT 1
)
"""

_SCHEMA_DOMAINS = """
CREATE TABLE IF NOT EXISTS domains (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    need_proxy   INTEGER,  -- NULL=无规则(跟随全局), 1=用代理, 0=强制直连
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_SCHEMA_TAGS = """
CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_SCHEMA_RECORD_TAGS = """
CREATE TABLE IF NOT EXISTS record_tags (
    record_url TEXT NOT NULL,
    tag_id     INTEGER NOT NULL,
    PRIMARY KEY (record_url, tag_id),
    FOREIGN KEY (record_url) REFERENCES records(url) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
)
"""

_SCHEMA_PROXY_RULES = """
CREATE TABLE IF NOT EXISTS proxy_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern    TEXT NOT NULL UNIQUE,        -- "*.google.com" / "github.com/*"
    need_proxy INTEGER NOT NULL DEFAULT 1,  -- 1=用全局代理, 0=强制直连
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# 代理只表达「要不要走」，具体地址仅存于全局配置一处。
# 旧表存的是代理地址（proxy TEXT）：
#   proxy_rules 旧语义 proxy='' = 强制直连 → need_proxy=0
#   domains     旧语义 proxy='' = 无规则跟随全局 → need_proxy=NULL（不能迁成 0，否则全站被强制直连）
def _migrate_to_bool_proxy(conn: sqlite3.Connection) -> None:
    for table, empty_value in (("proxy_rules", "0"), ("domains", "NULL")):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if "proxy" not in cols:
            continue
        if "need_proxy" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN need_proxy INTEGER")
        conn.execute(
            f"UPDATE {table} SET need_proxy = CASE WHEN proxy != '' THEN 1 ELSE {empty_value} END"
        )
        conn.execute(f"ALTER TABLE {table} DROP COLUMN proxy")


def _ensure(conn: sqlite3.Connection) -> None:
    with _ENSURE_LOCK:
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA_RECORDS)
    conn.execute(_SCHEMA_DOMAINS)
    conn.execute(_SCHEMA_TAGS)
    conn.execute(_SCHEMA_RECORD_TAGS)
    conn.execute(_SCHEMA_PROXY_RULES)
    conn.execute("PRAGMA journal_mode=WAL")
    _migrate_to_bool_proxy(conn)

    # 迁移：fetched 列（0=快照导入未抓取，1=已抓取/尝试过）。
    # ADD COLUMN 带 NOT NULL DEFAULT 合法，现有行自动得 1（都尝试过抓取），无需回填。
    try:
        conn.execute("SELECT fetched FROM records LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE records ADD COLUMN fetched INTEGER NOT NULL DEFAULT 1")

    # 迁移：group_name 列 → tags 表（一次性）
    try:
        rows = conn.execute("SELECT url, group_name FROM records WHERE group_name != ''").fetchall()
        for row in rows:
            tag_name = row["group_name"]
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
            tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO record_tags (record_url, tag_id) VALUES (?, ?)",
                         (row["url"], tag_id))
        conn.execute("ALTER TABLE records DROP COLUMN group_name")
    except sqlite3.OperationalError:
        pass  # group_name 列已不存在

    # 迁移：域名级配置（每域名的显示偏好 + 抓取规则），都挂在 domains 表上。
    # auto_fetch / cover 用**可空**整型——NULL 就是「跟随全局 / 默认」，
    # 与 need_proxy 的三态口径一致（NOT NULL 会存不进 NULL）。
    cols = [r[1] for r in conn.execute("PRAGMA table_info(domains)")]
    for col, ddl in (
        ("prefs", "TEXT NOT NULL DEFAULT '{}'"),          # JSON：{sort, fields[], tag}
        ("auto_fetch", "INTEGER"),                        # NULL=跟随全局, 1=开, 0=关（补抓不碰）
        ("detail_selector", "TEXT NOT NULL DEFAULT ''"),  # '' = 用默认 .space-y-2 > *
        ("cover", "INTEGER"),                             # NULL=默认(og), 0=不要封面, 1=只要图标
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE domains ADD COLUMN {col} {ddl}")

    # 自动填充 domains 表（老库升级用，**按库跑一次**）
    # 这段必须门控：_ensure 每开一条连接都会走一遍，全表扫 + 逐行 urlparse +
    # 写语句就是 O(N)×每条连接——一次 5000 条的自动补抓会放大成 O(N²)，
    # 且让每次连接都去抢写锁，跟真正的 upsert 抢 timeout=10。
    # 之后新增记录的写入路径（upsert_record / insert_quick_records / update_domain）
    # 都会同步补自己那条域名，这里的全表回填只服务「domains 表刚建起来的老库」。
    db = storage.db_path()
    if str(db) not in _DOMAINS_BACKFILLED_FOR:
        for row in conn.execute("SELECT DISTINCT url FROM records").fetchall():
            domain = root_domain(row["url"])
            if domain:
                conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
        # 多线程同时首开也安全：INSERT OR IGNORE 幂等，最多重复跑一次
        _DOMAINS_BACKFILLED_FOR.add(str(db))


@contextmanager
def conn():
    """短连接上下文：WAL + timeout=10，每次打开都先跑 schema/迁移。"""
    db = storage.db_path()
    if not db.parent.is_dir():
        # 目录不存在就创建（文件夹被删 / 外置盘重新挂载后自动重建，直接可用）
        db.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        _ensure(connection)
        with connection:
            yield connection
    finally:
        connection.close()
