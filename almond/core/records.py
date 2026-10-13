"""书签记录存储：记录 + 标签 + 代理规则的 CRUD（SQLite）。

线程安全：每次操作独立短连接（`db.conn()`，WAL + timeout=10）。
存储目录/纪元见 storage.py；建表迁移见 db.py；域名配置见 domains.py。

**合并规则**（`upsert_record`，按 url 去重）——改动存储层时必须保住的不变式：
抓取失败（success=False）绝不覆盖行内容，但置 fetched=1（「已抓取/**尝试过**」，
失败的快照记录不能永远灰着像还在排队）；空 title 不覆盖已有标题（手改标题存活）。
"""

import json
import sqlite3

from almond.core import db, storage
from almond.core.hostutil import root_domain


# ---------------------------------------------------------------- 行/标签装配

def _tags_for(conn: sqlite3.Connection, url: str) -> list[dict]:
    """单条记录的标签（各调用方自己查，避免 list_records 出现 N+1）。"""
    rows = conn.execute(
        "SELECT t.id, t.name FROM tags t "
        "JOIN record_tags rt ON rt.tag_id = t.id "
        "WHERE rt.record_url = ? ORDER BY t.name",
        (url,),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def _row_to_record(row: sqlite3.Row, tags: list[dict]) -> dict:
    return {
        "url": row["url"],
        "title": row["title"],
        "thumbnail": row["thumbnail"],
        "favicon": row["favicon"],
        "success": bool(row["success"]),
        "details": json.loads(row["details"] or "[]"),
        "domain": root_domain(row["url"]),
        "tags": tags,
        "fetched": bool(row["fetched"]),
    }


def _all_tags_map(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """一次 JOIN 取全部记录的标签，返回 {record_url: [tag]} —— 消除 N+1。"""
    result: dict[str, list[dict]] = {}
    rows = conn.execute(
        "SELECT rt.record_url, t.id, t.name FROM record_tags rt "
        "JOIN tags t ON t.id = rt.tag_id ORDER BY t.name"
    ).fetchall()
    for r in rows:
        result.setdefault(r["record_url"], []).append({"id": r["id"], "name": r["name"]})
    return result


# ──────────────────────── Records ────────────────────────

def count_records() -> int:
    """记录总数。切库响应只需要个数——list_records() 会拉全量行再 JOIN 标签，纯浪费。"""
    with db.conn() as conn:
        return conn.execute("SELECT count(*) FROM records").fetchone()[0]


def list_records(limit: int | None = None, offset: int = 0) -> list[dict]:
    """全部记录（或分页片段），按插入顺序。标签用一次 JOIN 取，避免 N+1。

    limit 为 None → 返回全量（兼容旧调用 / 切库 / 子进程读取）；
    否则返回 [offset, offset+limit) 的片段，供前端分页拉取（4000 条量级避免一次性
    把整张表与全部标签塞进一个 HTTP 响应）。
    """
    with db.conn() as conn:
        if limit is None:
            rows = conn.execute("SELECT * FROM records ORDER BY rowid").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM records ORDER BY rowid LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        tag_map = _all_tags_map(conn)
        return [_row_to_record(row, tag_map.get(row["url"], [])) for row in rows]


def list_pending_urls(limit: int = 5000) -> list[str]:
    """从未抓取过的记录（fetched=0），按插入顺序，只回 URL。

    自动补抓的轮询数据源：页面加载时只扫一次 state.records，而别的标签页、
    浏览器插件、导入接口随后写进库的链接不会自己冒出来，靠这个小接口发现。
    只回 url 不回整条记录——1378 条全量 /api/records 每 10 秒拉一次太重。
    """
    with db.conn() as conn:
        rows = conn.execute(
            "SELECT url FROM records WHERE fetched = 0 ORDER BY rowid LIMIT ?",
            (limit,),
        ).fetchall()
        return [r["url"] for r in rows]


def upsert_record(new: dict) -> dict:
    """按 URL 合并写入；抓取失败或标题为空时保留已有数据。置 fetched=1（真实抓取过）。"""
    with db.conn() as conn:
        old = conn.execute("SELECT * FROM records WHERE url = ?", (new["url"],)).fetchone()
        if old is not None and not new.get("success"):
            # 抓取失败：内容一个字都不覆盖（合并规则），但要把 fetched 置 1——
            # 这一列的语义是「已抓取/**尝试过**」。不记的话，快照导入的链接
            # 提取失败后 fetched 仍是 0，状态点永远灰着，看着像还在排队。
            conn.execute("UPDATE records SET fetched = 1 WHERE url = ?", (new["url"],))
            row = conn.execute("SELECT * FROM records WHERE url = ?", (new["url"],)).fetchone()
            return _row_to_record(row, _tags_for(conn, new["url"]))
        title = new.get("title", "") or (old["title"] if old is not None else "")
        conn.execute(
            "INSERT INTO records (url, title, thumbnail, favicon, success, details, fetched) "
            "VALUES (?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(url) DO UPDATE SET title = excluded.title, "
            "thumbnail = excluded.thumbnail, favicon = excluded.favicon, "
            "success = excluded.success, details = excluded.details, "
            "fetched = 1",
            (
                new["url"],
                title,
                new.get("thumbnail", ""),
                new.get("favicon", ""),
                1 if new.get("success") else 0,
                json.dumps(new.get("details") or [], ensure_ascii=False),
            ),
        )
        # 自动填充 domains 表
        domain = root_domain(new["url"])
        if domain:
            conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
        row = conn.execute("SELECT * FROM records WHERE url = ?", (new["url"],)).fetchone()
        return _row_to_record(row, _tags_for(conn, new["url"]))


def upsert_record_if_epoch(new: dict, epoch: int) -> dict | None:
    """带存储纪元的合并写入：纪元一致才写，不一致返回 None（本次结果作废）。

    比对与写入在 `storage.SWITCH_LOCK` 下一体执行、与 `set_storage_dir` 互斥——
    杜绝「调用方比对纪元通过 → 切库 → upsert 落进**新库**」的 TOCTOU 窗口。
    `/api/fetch` 的抓取结果必须走这个入口（直接调 `upsert_record` 的脚本
    路径没有这层保护，但它们也不经过切库流程）。
    """
    with storage.SWITCH_LOCK:
        if epoch != storage.storage_epoch():
            return None
        return upsert_record(new)


def insert_quick_records(items: list[dict]) -> dict:
    """快照批量入库：**不联网**，直接写入已有元数据，`fetched=0`。

    items: [{url, title?, thumbnail?, favicon?, tags?: [str]}]
    单事务处理，已存在的 URL 跳过。返回 {inserted, skipped}。
    """
    inserted = skipped = 0
    with db.conn() as conn:
        for item in items:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            if conn.execute("SELECT 1 FROM records WHERE url = ?", (url,)).fetchone():
                skipped += 1
                continue
            # success=1：HTML 已提供标题/封面，元数据有效（details 留待按需补抓）
            conn.execute(
                "INSERT INTO records (url, title, thumbnail, favicon, success, details, fetched) "
                "VALUES (?, ?, ?, ?, 1, '[]', 0)",
                (
                    url,
                    (item.get("title") or "").strip(),
                    (item.get("thumbnail") or "").strip(),
                    (item.get("favicon") or "").strip(),
                ),
            )
            domain = root_domain(url)
            if domain:
                conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
            for name in item.get("tags") or []:
                name = (name or "").strip()
                if not name:
                    continue
                conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
                tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()[0]
                conn.execute(
                    "INSERT OR IGNORE INTO record_tags (record_url, tag_id) VALUES (?, ?)",
                    (url, tag_id),
                )
            inserted += 1
    return {"inserted": inserted, "skipped": skipped}


def set_title(url: str, title: str) -> dict | None:
    with db.conn() as conn:
        cur = conn.execute("UPDATE records SET title = ? WHERE url = ?", (title.strip(), url))
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM records WHERE url = ?", (url,)).fetchone()
        return _row_to_record(row, _tags_for(conn, url))


def delete_record(url: str) -> bool:
    with db.conn() as conn:
        cur = conn.execute("DELETE FROM records WHERE url = ?", (url,))
        return cur.rowcount > 0


# ──────────────────────── Proxy rules ────────────────────────
# 规则表的 CRUD 留在本模块（storage 层的数据入口），匹配/判定逻辑在 proxy.py。

def _rule_row(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "pattern": row["pattern"],
            "need_proxy": bool(row["need_proxy"])}


def list_proxy_rules() -> list[dict]:
    with db.conn() as conn:
        rows = conn.execute("SELECT * FROM proxy_rules ORDER BY length(pattern) DESC, pattern").fetchall()
        return [_rule_row(r) for r in rows]


def upsert_proxy_rule(pattern: str, need_proxy: bool) -> dict:
    with db.conn() as conn:
        conn.execute(
            "INSERT INTO proxy_rules (pattern, need_proxy) VALUES (?, ?) "
            "ON CONFLICT(pattern) DO UPDATE SET need_proxy = excluded.need_proxy",
            (pattern.strip(), 1 if need_proxy else 0),
        )
        row = conn.execute("SELECT * FROM proxy_rules WHERE pattern = ?", (pattern.strip(),)).fetchone()
        return _rule_row(row)


def delete_proxy_rule(rule_id: int) -> bool:
    with db.conn() as conn:
        cur = conn.execute("DELETE FROM proxy_rules WHERE id = ?", (rule_id,))
        return cur.rowcount > 0


def proxy_decision_inputs() -> dict:
    """代理判定所需的整批输入，供 `proxy.list_proxy_domains` 做纯判定循环。

    {names: 根域名候选集, rules: 代理规则（长模式在前）, domain_need: 域名三态开关}
    单连接取数、内存判定——避免逐域名开库触发 _ensure 全表扫描。
    """
    with db.conn() as conn:
        names = {row["name"] for row in conn.execute("SELECT name FROM domains")}
        for row in conn.execute("SELECT DISTINCT url FROM records"):
            d = root_domain(row["url"])
            if d:
                names.add(d)
        rules = [
            {"pattern": r["pattern"], "need_proxy": bool(r["need_proxy"])}
            for r in conn.execute(
                "SELECT pattern, need_proxy FROM proxy_rules ORDER BY length(pattern) DESC, pattern"
            ).fetchall()
        ]
        domain_need = {
            row["name"]: row["need_proxy"]
            for row in conn.execute("SELECT name, need_proxy FROM domains")
        }
    return {"names": names, "rules": rules, "domain_need": domain_need}


# ──────────────────────── Tags ────────────────────────

def list_tags() -> list[dict]:
    with db.conn() as conn:
        rows = conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]


def create_tag(name: str) -> dict:
    with db.conn() as conn:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name.strip(),))
        row = conn.execute("SELECT * FROM tags WHERE name = ?", (name.strip(),)).fetchone()
        return {"id": row["id"], "name": row["name"]}


def delete_tag(tag_id: int) -> bool:
    with db.conn() as conn:
        cur = conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        return cur.rowcount > 0


def add_tag_to_record(url: str, tag_id: int) -> bool:
    with db.conn() as conn:
        try:
            conn.execute("INSERT OR IGNORE INTO record_tags (record_url, tag_id) VALUES (?, ?)", (url, tag_id))
            return True
        except sqlite3.IntegrityError:
            return False


def remove_tag_from_record(url: str, tag_id: int) -> bool:
    with db.conn() as conn:
        cur = conn.execute("DELETE FROM record_tags WHERE record_url = ? AND tag_id = ?", (url, tag_id))
        return cur.rowcount > 0
