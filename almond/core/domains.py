"""域名配置与域名重置。

`domains` 表是每域名的配置仓：显示名 / 代理三态（NULL 跟随全局，1 用代理，0 强制直连）/
自动补抓开关 / 详情选择器与提取形态（detail_selector + detail_mode）/ 封面规则 /
子页显示偏好（prefs）。
`replace_domain` 在 TLD 失效时把整个域名（含子域名）在库里的引用整体改写成新域名。
"""

import json
import re
import sqlite3
from urllib.parse import urlparse

from almond.core import db
from almond.core.hostutil import root_domain

_UNSET = object()  # 与「值为 None」区分开：None = 设为无规则，_UNSET = 本次不改


def _domain_row(row) -> dict:
    # prefs 落库是 JSON 字符串，出库还原成对象（坏数据一律当没有偏好）
    try:
        prefs = json.loads(row["prefs"] or "{}")
    except (json.JSONDecodeError, KeyError, IndexError):
        prefs = {}
    if not isinstance(prefs, dict):
        prefs = {}
    return {"id": row["id"], "name": row["name"],
            "display_name": row["display_name"],
            # None = 无规则（跟随全局），True = 用代理，False = 强制直连
            "need_proxy": None if row["need_proxy"] is None else bool(row["need_proxy"]),
            # 每域名显示偏好（{sort, fields[], tag}）与抓取规则
            "prefs": prefs,
            "auto_fetch": None if row["auto_fetch"] is None else bool(row["auto_fetch"]),
            "detail_selector": row["detail_selector"] or "",
            # 详情提取形态：''=「标签: 值」行（默认），'tags'=扁平标签链，'og'=OG 快速模式
            "detail_mode": row["detail_mode"] or "",
            "cover": None if row["cover"] is None else bool(row["cover"])}


def list_domains() -> list[dict]:
    with db.conn() as conn:
        rows = conn.execute("SELECT * FROM domains ORDER BY name").fetchall()
        return [_domain_row(r) for r in rows]


def update_domain(name: str, display_name=_UNSET, need_proxy=_UNSET,
                  prefs=_UNSET, auto_fetch=_UNSET, detail_selector=_UNSET,
                  detail_mode=_UNSET, cover=_UNSET) -> dict:
    with db.conn() as conn:
        # 确保域名存在
        conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (name,))
        if display_name is not _UNSET:
            conn.execute("UPDATE domains SET display_name = ? WHERE name = ?",
                         (display_name or "", name))
        if need_proxy is not _UNSET:
            # None → NULL（无规则），True/False → 1/0
            value = None if need_proxy is None else (1 if need_proxy else 0)
            conn.execute("UPDATE domains SET need_proxy = ? WHERE name = ?", (value, name))
        if prefs is not _UNSET:
            if prefs is None:
                prefs = {}
            if not isinstance(prefs, dict):
                raise ValueError("prefs 必须是对象")
            conn.execute("UPDATE domains SET prefs = ? WHERE name = ?",
                         (json.dumps(prefs, ensure_ascii=False), name))
        if auto_fetch is not _UNSET:
            value = None if auto_fetch is None else (1 if auto_fetch else 0)
            conn.execute("UPDATE domains SET auto_fetch = ? WHERE name = ?", (value, name))
        if detail_selector is not _UNSET:
            conn.execute("UPDATE domains SET detail_selector = ? WHERE name = ?",
                         (detail_selector or "", name))
        if detail_mode is not _UNSET:
            # 只认 '' / 'tags' / 'og' 三个值：其余（含历史脏数据）一律归一成 ''
            mode = detail_mode if detail_mode in ("", "tags", "og") else ""
            conn.execute("UPDATE domains SET detail_mode = ? WHERE name = ?", (mode, name))
        if cover is not _UNSET:
            value = None if cover is None else (1 if cover else 0)
            conn.execute("UPDATE domains SET cover = ? WHERE name = ?", (value, name))
        row = conn.execute("SELECT * FROM domains WHERE name = ?", (name,)).fetchone()
        return _domain_row(row)


def get_domain_need_proxy(url: str) -> bool | None:
    """返回该域名是否要走代理；无域名规则时返回 None（由全局代理兜底）。"""
    domain = root_domain(url)
    if not domain:
        return None
    with db.conn() as conn:
        row = conn.execute("SELECT need_proxy FROM domains WHERE name = ?", (domain,)).fetchone()
        if row is None or row["need_proxy"] is None:
            return None
        return bool(row["need_proxy"])


def get_domain_fetch_config(url: str) -> dict:
    """该域名的抓取规则（`/api/fetch` 用）：详情选择器 + 提取形态 + 封面开关。
    **故意不返回代理结论**——代理优先级由 `proxy.resolve_proxy` 统一算
    （URL 模式规则 > 域名规则 > 全局），在这里拿 need_proxy 去覆盖它会打乱优先级。"""
    out = {"detail_selector": "", "detail_mode": "", "cover": None}
    domain = root_domain(url)
    if not domain:
        return out
    with db.conn() as conn:
        row = conn.execute(
            "SELECT detail_selector, detail_mode, cover FROM domains WHERE name = ?", (domain,)
        ).fetchone()
    if row is None:
        return out
    return {"detail_selector": row["detail_selector"] or "",
            "detail_mode": row["detail_mode"] or "",
            "cover": None if row["cover"] is None else bool(row["cover"])}


# ──────────────────────── 域名重置 ────────────────────────

# 至少两段（允许 .com.cn 这类整体作 TLD 判断不在此处，格式校验只看形状）
_DOMAIN_SHAPE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$",
    re.I,
)


def _norm_domain(raw: str) -> str:
    """归一化用户输入：小写、剥掉 scheme/路径（粘贴完整 URL 也能用）。"""
    d = (raw or "").strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip()


def _swap_url_domain(url: str, old: str, new: str) -> str:
    """改写 URL 的主机名：old 及其子域名（cdn.old → cdn.new）换掉，其余原样。"""
    try:
        p = urlparse(url)
        host = p.hostname or ""
    except Exception:
        return url
    low = host.lower()
    if low == old:
        new_host = new
    elif low.endswith("." + old):
        new_host = host[: -len(old)] + new   # 保留子域名前缀及其原始大小写
    else:
        return url
    netloc = new_host
    try:
        if p.port:
            netloc += f":{p.port}"
    except ValueError:
        pass  # 非法端口：原样拼回，不因它中断整次重置
    if "@" in (p.netloc or ""):
        netloc = p.netloc.rsplit("@", 1)[0] + "@" + netloc
    return p._replace(netloc=netloc).geturl()


def _swap_text_domain(text: str, old: str, new: str) -> str:
    """文本（缩略图/favicon/详情 JSON/规则模式）中的域名按边界整体替换。

    边界：前面不是域名标签字符（否则 myexample.live 不会误中），
    后面不能延伸出更长的域名（example.live.backup.com 不是本域，不替换）。
    """
    if not text or old not in text.lower():
        return text
    pattern = re.compile(
        r"(?<![a-z0-9-])" + re.escape(old) + r"(?![a-z0-9.-]*[a-z0-9])",
        re.I,
    )
    return pattern.sub(new, text)


def replace_domain(old: str, new: str) -> dict:
    """域名重置：原域名失效时，把它（含子域名）在库里的所有引用换成新域名。

    覆盖：records 的 url / thumbnail / favicon / details、record_tags 关联、
    domains 表（别名与域名代理规则）、proxy_rules 中的匹配模式。
    新旧 URL 撞车（两个域名版本都导入过）时合并到新记录：标签迁过去，删旧行。
    单事务，整体出错回滚；单条记录出错只回滚那一条、计为 failed，其余照改。
    返回 {total, records, merged, failed, error, rules}，恒有
    total == records + merged + failed：
      total   = 命中（主机名是 old 或其子域名）的记录数，即本次重置对象；
      records = 改写成功；
      merged  = 重复（新域名版本已存在，并进那一条，旧行删除）；
      failed  = 命中却没改成的（单条出错被跳过），error 给出最近一条原因。
    """
    old = _norm_domain(old)
    new = _norm_domain(new)
    if not old or not new:
        raise ValueError("域名不能为空")
    if not _DOMAIN_SHAPE.match(old):
        raise ValueError(f"原域名格式无效：{old}")
    if not _DOMAIN_SHAPE.match(new):
        raise ValueError(f"新域名格式无效：{new}")
    if old == new:
        raise ValueError("新域名与原域名相同")

    with db.conn() as conn:
        # 显式开外层事务：行级 SAVEPOINT 必须挂在它下面——否则最外层 savepoint 一 RELEASE
        # 就把已改的行提交了，「整体出错回滚」会退化成逐条落库；挂上去之后单条 RELEASE
        # 只释放该条，整体仍由 with conn: 一次性收口（出错回滚、正常提交）
        if not conn.in_transaction:
            conn.execute("BEGIN")
        total = updated = merged = rules_updated = 0
        last_error = None

        for row in conn.execute(
            "SELECT url, thumbnail, favicon, details FROM records"
        ).fetchall():
            new_url = _swap_url_domain(row["url"], old, new)
            if new_url == row["url"]:
                # 链接没命中（只有封面/详情里出现过原域名）：照改字段，但不计入重置对象
                thumb = _swap_text_domain(row["thumbnail"], old, new)
                favicon = _swap_text_domain(row["favicon"], old, new)
                details = _swap_text_domain(row["details"], old, new)
                if (thumb, favicon, details) != (row["thumbnail"], row["favicon"], row["details"]):
                    conn.execute(
                        "UPDATE records SET thumbnail = ?, favicon = ?, details = ? WHERE url = ?",
                        (thumb, favicon, details, new_url),
                    )
                continue

            total += 1
            conn.execute("SAVEPOINT xr_row")
            try:
                if conn.execute(
                    "SELECT 1 FROM records WHERE url = ?", (new_url,)
                ).fetchone():
                    # 新域名版本已存在：标签并过去，删掉旧记录（它的封面等随行作废）
                    conn.execute(
                        "UPDATE OR IGNORE record_tags SET record_url = ? WHERE record_url = ?",
                        (new_url, row["url"]),
                    )
                    conn.execute("DELETE FROM record_tags WHERE record_url = ?", (row["url"],))
                    conn.execute("DELETE FROM records WHERE url = ?", (row["url"],))
                    merged += 1
                else:
                    conn.execute(
                        "UPDATE records SET url = ? WHERE url = ?", (new_url, row["url"])
                    )
                    # OR IGNORE：新旧记录共有标签时主键不冲突，独有标签照常迁走
                    conn.execute(
                        "UPDATE OR IGNORE record_tags SET record_url = ? WHERE record_url = ?",
                        (new_url, row["url"]),
                    )
                    thumb = _swap_text_domain(row["thumbnail"], old, new)
                    favicon = _swap_text_domain(row["favicon"], old, new)
                    details = _swap_text_domain(row["details"], old, new)
                    if (thumb, favicon, details) != (
                        row["thumbnail"], row["favicon"], row["details"]
                    ):
                        conn.execute(
                            "UPDATE records SET thumbnail = ?, favicon = ?, details = ? "
                            "WHERE url = ?",
                            (thumb, favicon, details, new_url),
                        )
                    updated += 1
            except sqlite3.Error as exc:
                # 这一条出错只丢这一条：退回行级 savepoint，其余记录照常改
                # （非 sqlite3 异常照旧冒泡，由 with conn: 整体回滚）
                conn.execute("ROLLBACK TO xr_row")
                conn.execute("RELEASE xr_row")
                last_error = f"{row['url']}：{exc}"
            else:
                conn.execute("RELEASE xr_row")

        # domains 表：改名保住别名/代理规则；new 已有行则把 old 的设置（仅在 new 缺时）并过去
        old_row = conn.execute("SELECT * FROM domains WHERE name = ?", (old,)).fetchone()
        if old_row is not None:
            new_row = conn.execute("SELECT * FROM domains WHERE name = ?", (new,)).fetchone()
            if new_row is None:
                conn.execute("UPDATE domains SET name = ? WHERE name = ?", (new, old))
            else:
                if new_row["need_proxy"] is None and old_row["need_proxy"] is not None:
                    conn.execute(
                        "UPDATE domains SET need_proxy = ? WHERE name = ?",
                        (old_row["need_proxy"], new),
                    )
                if not new_row["display_name"] and old_row["display_name"]:
                    conn.execute(
                        "UPDATE domains SET display_name = ? WHERE name = ?",
                        (old_row["display_name"], new),
                    )
                conn.execute("DELETE FROM domains WHERE name = ?", (old,))

        for row in conn.execute("SELECT id, pattern FROM proxy_rules").fetchall():
            new_pattern = _swap_text_domain(row["pattern"], old, new)
            if new_pattern == row["pattern"]:
                continue
            if conn.execute(
                "SELECT 1 FROM proxy_rules WHERE pattern = ?", (new_pattern,)
            ).fetchone():
                conn.execute("DELETE FROM proxy_rules WHERE id = ?", (row["id"],))
            else:
                conn.execute(
                    "UPDATE proxy_rules SET pattern = ? WHERE id = ?", (new_pattern, row["id"])
                )
            rules_updated += 1

        return {"old": old, "new": new, "total": total,
                "records": updated, "merged": merged,
                "failed": total - updated - merged, "error": last_error,
                "rules": rules_updated}
