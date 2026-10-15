"""目录浏览：存储面板「浏览…」点选器的数据源。

原来是 `Handler._fs_list` 方法，但它一个 `self` 都没用到 —— 拆成模块级函数，
Handler 只负责把返回值包成 JSON。
"""

import os
from pathlib import Path


def fs_list(raw: str) -> dict:
    """目录浏览：空 path 返回顶层（Windows 列盘符，POSIX 列根目录）。"""
    if not raw:
        if os.name == "nt":
            roots = [f"{c}:\\" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{c}:\\").exists()]
        else:
            roots = ["/"]
        return {"ok": True, "path": "", "parent": "", "entries": roots}
    p = Path(raw).expanduser()
    try:
        p = p.resolve()
        if p.is_file():
            p = p.parent
        entries = sorted((d.name for d in p.iterdir() if d.is_dir()), key=str.lower)
    except OSError as exc:
        return {"ok": False, "error": f"无法读取目录：{exc}"}
    parent = str(p.parent) if p.parent != p else ""
    return {"ok": True, "path": str(p), "parent": parent, "entries": entries[:500]}
