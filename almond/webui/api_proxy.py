"""/api/proxy-* —— 代理规则 CRUD、侧栏徽标、本机端口探测。"""

from almond.core.proxy import current_proxy, detect_working_proxy, list_proxy_domains
from almond.core.records import delete_proxy_rule, list_proxy_rules, upsert_proxy_rule
from almond.webui.api_util import send_json


def rules(h, qs: dict, body: dict) -> None:
    """GET /api/proxy-rules：URL 通配符规则列表。"""
    send_json(h, {"rules": list_proxy_rules()})


def upsert_rule(h, qs: dict, body: dict) -> None:
    """POST /api/proxy-rule：新增 / 更新规则。"""
    pattern = (body.get("pattern") or "").strip()
    if not pattern:
        send_json(h, {"ok": False, "error": "缺少匹配模式"}, 400)
        return
    rule = upsert_proxy_rule(pattern, bool(body.get("need_proxy", True)))
    send_json(h, {"ok": True, "rule": rule})


def remove_rule(h, qs: dict, body: dict) -> None:
    """DELETE /api/proxy-rule?id=…：删除规则。"""
    rule_id = (qs.get("id") or [""])[0]
    if not rule_id or not delete_proxy_rule(int(rule_id)):
        send_json(h, {"ok": False, "error": "规则不存在"}, 404)
        return
    send_json(h, {"ok": True})


def proxied_domains(h, qs: dict, body: dict) -> None:
    """GET /api/proxy-domains：最终会走代理的根域名（侧栏「代理」徽标数据源）。"""
    send_json(h, {"domains": list_proxy_domains()})


def detect(h, qs: dict, body: dict) -> None:
    """POST /api/proxy-detect：探测本机 7889-7899 并**验证转发可用**（坏端口不回填）。"""
    send_json(h, {"ok": True, "proxy": detect_working_proxy()})


def current(h, qs: dict, body: dict) -> None:
    """GET /api/proxy-current：后台定时线程最近一次探测结论。

    只读 `current_proxy()` 内存缓存、**不发起探测**——给前端 30s 轮询做被动跟随；
    要主动探测（点「检测」按钮）走 /api/proxy-detect。
    """
    send_json(h, {"proxy": current_proxy()})


ROUTES = {
    ("GET", "/api/proxy-rules"): rules,
    ("POST", "/api/proxy-rule"): upsert_rule,
    ("DELETE", "/api/proxy-rule"): remove_rule,
    ("GET", "/api/proxy-domains"): proxied_domains,
    ("POST", "/api/proxy-detect"): detect,
    ("GET", "/api/proxy-current"): current,
}
