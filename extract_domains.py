#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Almond 的 metadata.db 提取域名，每个域名生成一条完整网址。

用法（在仓库根目录执行）：
    python extract_domains.py
    python extract_domains.py --from records      # 改为从书签 URL 提取去重后的主机域名
    python extract_domains.py --db "D:/XingRenCache/metadata.db"
    python extract_domains.py --scheme http --no-trailing-slash --output my_urls.txt

输出：每行一条 https://<域名>/ ，并打印总数。仅用标准库，打开数据库为只读模式，
不会锁住正在运行的服务。数据库路径解析与程序一致：
    --db 显式指定 > 环境变量 ALMOND_DATA_DIR > config.json 的 storage_dir > 仓库根。
"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse


def resolve_db_path(explicit: str | None) -> Path:
    """按程序同样的优先级定位 metadata.db。"""
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            sys.exit(f"[X] 指定的数据库不存在：{p}")
        return p

    candidates: list[Path] = []
    env = os.environ.get("ALMOND_DATA_DIR")
    if env:
        candidates.append(Path(env).expanduser())

    here = Path(__file__).resolve().parent
    # 在脚本所在目录（及其上级）找 config.json，读它的 storage_dir
    for d in (here, here.parent):
        cfg = d / "config.json"
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict) and data.get("storage_dir"):
                    candidates.append(Path(data["storage_dir"]).expanduser())
            except (OSError, json.JSONDecodeError):
                pass
            break  # 只认第一个找到的 config.json

    candidates.append(here)  # 兜底：仓库根

    for c in candidates:
        db = Path(c) / "metadata.db"
        if db.exists():
            return db

    sys.exit(
        "[X] 找不到 metadata.db。\n"
        "    可用下列任一方式指定：\n"
        "      • 设置环境变量 ALMOND_DATA_DIR 指向存储目录；\n"
        "      • 确保仓库根有 config.json（含 storage_dir）；\n"
        "      • 用 --db 显式传入 metadata.db 路径。"
    )


def read_only_connect(db_path: Path) -> sqlite3.Connection:
    # mode=ro 打开，避免锁住正在运行的服务；immutable=1 时都不要求写权限
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def domains_from_table(conn: sqlite3.Connection) -> list[str]:
    if not _has_table(conn, "domains"):
        sys.exit("[X] 数据库中没有 domains 表。")
    rows = conn.execute("SELECT name FROM domains ORDER BY name").fetchall()
    return [r["name"].strip() for r in rows if (r["name"] or "").strip()]


def hosts_from_records(conn: sqlite3.Connection) -> list[str]:
    if not _has_table(conn, "records"):
        sys.exit("[X] 数据库中没有 records 表。")
    seen: set[str] = set()
    out: list[str] = []
    for r in conn.execute("SELECT url FROM records").fetchall():
        url = (r["url"] or "").strip()
        if not url:
            continue
        host = urlparse(url).hostname
        if not host:
            continue
        host = host.lower()
        if host in seen:
            continue
        seen.add(host)
        out.append(host)
    out.sort()
    return out


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def main() -> None:
    ap = argparse.ArgumentParser(description="从 metadata.db 提取域名并生成网址")
    ap.add_argument("--db", help="显式指定 metadata.db 路径（优先级最高）")
    ap.add_argument("--from", dest="source", choices=["domains", "records"],
                    default="domains",
                    help="数据来源：domains=域名配置表（默认）；records=从书签 URL 提取去重主机")
    ap.add_argument("--scheme", default="https", help="网址协议（默认 https）")
    ap.add_argument("--no-trailing-slash", action="store_true", help="网址末尾不加 /")
    ap.add_argument("--output", default="domains_urls.txt", help="输出文件名（默认 domains_urls.txt）")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db)
    conn = read_only_connect(db_path)

    names = domains_from_table(conn) if args.source == "domains" else hosts_from_records(conn)
    conn.close()

    if not names:
        print(f"[!] 在 {db_path} 的 {args.source} 中没有找到任何域名。")
        return

    trailing = "" if args.no_trailing_slash else "/"
    lines = [f"{args.scheme}://{name}{trailing}" for name in names]

    out_path = Path(args.output)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[*] 数据库：{db_path}")
    print(f"[*] 来源：{args.source} 表，共 {len(names)} 个域名")
    print(f"[*] 已写出：{out_path.resolve()}  （示例：{lines[0]} … {lines[-1]}）")


if __name__ == "__main__":
    main()
