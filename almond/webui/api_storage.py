"""/api/storage-*、/api/fs/list —— 存储目录切换、历史、目录浏览。"""

import time

from almond.core.log import logger
from almond.core.records import count_records
from almond.core.storage import get_storage_paths, remove_storage_history, set_storage_dir
from almond.webui import fsbrowse
from almond.webui.api_util import send_json


def paths(h, qs: dict, body: dict) -> None:
    """GET /api/storage-dir：当前存储目录 / 数据库 / 缓存路径 + 历史 + epoch。"""
    send_json(h, get_storage_paths())


def switch(h, qs: dict, body: dict) -> None:
    """POST /api/storage-dir：切换存储目录（**不迁移**：旧数据留原地，新目录从零开始）。"""
    new_dir = (body.get("path") or "").strip()
    t0 = time.monotonic()
    try:
        result = set_storage_dir(new_dir)
    except (ValueError, OSError) as exc:
        send_json(h, {"ok": False, "error": str(exc)}, 400)
        return
    t1 = time.monotonic()
    n = count_records()
    # 分段计时：切换慢时先看是 set（配置/目标库校验）还是 count（新库首开
    # 的 domains 回填在 _ensure 里）在耗时，不用猜
    logger.info(
        f"存储目录已切换 → {result['path']}（set {int((t1 - t0) * 1000)}ms，"
        f"count {int((time.monotonic() - t1) * 1000)}ms，{n} 条）"
    )
    send_json(h, {"ok": True, **result, "records": n})


def drop_history(h, qs: dict, body: dict) -> None:
    """DELETE /api/storage-history?path=…：从历史位置移除一条（当前目录移不掉）。"""
    removed = (qs.get("path") or [""])[0]
    send_json(h, {"ok": True, "history": remove_storage_history(removed)})


def fs_list(h, qs: dict, body: dict) -> None:
    """GET /api/fs/list?path=…：目录浏览（空 path 返回盘符 / 根，供界面点选目录）。"""
    send_json(h, fsbrowse.fs_list((qs.get("path") or [""])[0]))


ROUTES = {
    ("GET", "/api/storage-dir"): paths,
    ("POST", "/api/storage-dir"): switch,
    ("DELETE", "/api/storage-history"): drop_history,
    ("GET", "/api/fs/list"): fs_list,
}
