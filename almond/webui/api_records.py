"""/api/records*、/api/record*、/api/tag* —— 记录与标签的读写入口。"""

from almond.core.records import (
    add_tag_to_record, count_records, create_tag, delete_record, delete_tag,
    insert_quick_records, list_pending_urls, list_records, list_tags,
    remove_tag_from_record, set_title,
)
from almond.core.storage import storage_epoch
from almond.webui.api_util import require_fields, send_json


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def list_all(h, qs: dict, body: dict) -> None:
    """GET /api/records：记录 + epoch（前端开抓前要带上）。

    epoch 一并返回：页面加载与切库后的 loadRecords 都会顺带刷新。

    带 `?limit=&offset=` 时分页返回 {"items","total","has_more","epoch"}，
    供前端按需分页拉取（4000 条量级不一次性塞满一个响应）；
    不带 limit 时保持旧响应 {"records","epoch"}，兼容其它调用方。
    """
    limit = _int((qs.get("limit") or [""])[0])
    offset = max(0, _int((qs.get("offset") or [""])[0]))
    if limit > 0:
        items = list_records(limit, offset)
        total = count_records()
        send_json(h, {
            "items": items,
            "total": total,
            "has_more": offset + limit < total,
            "epoch": storage_epoch(),
        })
        return
    send_json(h, {"records": list_records(), "epoch": storage_epoch()})


def list_pending(h, qs: dict, body: dict) -> None:
    """GET /api/records/pending：未抓取记录的 URL 列表（补抓队列的轮询源）。"""
    send_json(h, {"urls": list_pending_urls()})


def quick_insert(h, qs: dict, body: dict) -> None:
    """POST /api/records/quick：快照导入——只写已有元数据，不联网。"""
    items = body.get("items") or []
    if not isinstance(items, list) or not items:
        send_json(h, {"ok": False, "error": "缺少 items"}, 400)
        return
    result = insert_quick_records(items)
    send_json(h, {"ok": True, **result})


def rename(h, qs: dict, body: dict) -> None:
    """PATCH /api/record：重命名（title 是**存在性**校验——空标题合法）。"""
    url = (body.get("url") or "").strip()
    if "title" not in body:
        send_json(h, {"ok": False, "error": "缺少 title"}, 400)
        return
    record = set_title(url, body.get("title") or "")
    if record is None:
        send_json(h, {"ok": False, "error": "记录不存在"}, 404)
        return
    send_json(h, {"ok": True, "record": record})


def remove(h, qs: dict, body: dict) -> None:
    """DELETE /api/record?url=…：删除记录。"""
    url = (qs.get("url") or [""])[0]
    if not delete_record(url):
        send_json(h, {"ok": False, "error": "记录不存在"}, 404)
        return
    send_json(h, {"ok": True})


def tags(h, qs: dict, body: dict) -> None:
    """GET /api/tags：标签列表。"""
    send_json(h, {"tags": list_tags()})


def create(h, qs: dict, body: dict) -> None:
    """POST /api/tag：新建标签。"""
    name = (body.get("name") or "").strip()
    if not name:
        send_json(h, {"ok": False, "error": "缺少标签名"}, 400)
        return
    tag = create_tag(name)
    send_json(h, {"ok": True, "tag": tag})


def remove_tag(h, qs: dict, body: dict) -> None:
    """DELETE /api/tag?id=…：删除标签。"""
    tag_id = (qs.get("id") or [""])[0]
    if not tag_id or not delete_tag(int(tag_id)):
        send_json(h, {"ok": False, "error": "标签不存在"}, 404)
        return
    send_json(h, {"ok": True})


def tag_record(h, qs: dict, body: dict) -> None:
    """POST /api/record/tag：给记录打标签。"""
    url = (body.get("url") or "").strip()
    tag_id = body.get("tag_id")
    err = require_fields(body, "url", "tag_id")
    if err:
        send_json(h, {"ok": False, "error": err}, 400)
        return
    add_tag_to_record(url, tag_id)
    send_json(h, {"ok": True})


def untag_record(h, qs: dict, body: dict) -> None:
    """DELETE /api/record/tag?url=…&tag_id=…：移除记录标签。"""
    url = (qs.get("url") or [""])[0]
    tag_id = (qs.get("tag_id") or [""])[0]
    if not url or not tag_id:
        send_json(h, {"ok": False, "error": "缺少 url 或 tag_id"}, 400)
        return
    remove_tag_from_record(url, int(tag_id))
    send_json(h, {"ok": True})


ROUTES = {
    ("GET", "/api/records"): list_all,
    ("GET", "/api/records/pending"): list_pending,
    ("POST", "/api/records/quick"): quick_insert,
    ("PATCH", "/api/record"): rename,
    ("DELETE", "/api/record"): remove,
    ("GET", "/api/tags"): tags,
    ("POST", "/api/tag"): create,
    ("DELETE", "/api/tag"): remove_tag,
    ("POST", "/api/record/tag"): tag_record,
    ("DELETE", "/api/record/tag"): untag_record,
}
