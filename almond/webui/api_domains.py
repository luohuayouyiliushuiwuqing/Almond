"""/api/domains、/api/domain、/api/domain/replace —— 域名配置与域名重置。"""

from almond.core import fetch_hints
from almond.core.domains import list_domains, replace_domain, update_domain
from almond.webui.api_util import send_json


def domains(h, qs: dict, body: dict) -> None:
    """GET /api/domains：域名列表 + 系统实测结论（tested，只读）。

    tested = 抓取经验缓存里的系统实测结论（fetch_hints.all_hints）：
    可达性 / 方案 id / plan（自动补抓放行判据）/ confirm（确认结论），
    域名没有数据则为 null。
    """
    tested = fetch_hints.all_hints()
    rows = list_domains()
    for d in rows:
        d["tested"] = tested.get(d["name"])
    send_json(h, {"domains": rows})


def update(h, qs: dict, body: dict) -> None:
    """PATCH /api/domain：更新域名设置（只改给到的字段，_UNSET 语义）。"""
    name = (body.get("name") or "").strip()
    if not name:
        send_json(h, {"ok": False, "error": "缺少域名"}, 400)
        return
    # 只透传请求体里出现的字段（后端按 _UNSET 语义跳过没给的）：
    # need_proxy / auto_fetch / cover 是三态（null = 跟随全局 / 默认）；
    # detail_mode 只认 '' / 'tags' / 'og'（update_domain 归一化，脏值按 '' 落）
    kwargs = {}
    for key in ("display_name", "need_proxy", "prefs",
                "auto_fetch", "detail_selector", "detail_mode", "cover"):
        if key in body:
            kwargs[key] = body.get(key)
    try:
        domain = update_domain(name, **kwargs)
    except ValueError as exc:
        send_json(h, {"ok": False, "error": str(exc)}, 400)
        return
    send_json(h, {"ok": True, "domain": domain})


def replace(h, qs: dict, body: dict) -> None:
    """POST /api/domain/replace：域名重置（原域名含子域名整体换成新域名）。"""
    try:
        result = replace_domain(body.get("old") or "", body.get("new") or "")
    except ValueError as exc:
        send_json(h, {"ok": False, "error": str(exc)}, 400)
        return
    send_json(h, {"ok": True, **result})


ROUTES = {
    ("GET", "/api/domains"): domains,
    ("PATCH", "/api/domain"): update,
    ("POST", "/api/domain/replace"): replace,
}
