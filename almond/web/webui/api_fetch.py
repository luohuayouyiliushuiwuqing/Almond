"""/api/fetch、/api/fetch/status、/api/img、/api/confirm —— 抓取执行与图片代抓。

抓取的存储纪元协议三段住在这里：
① 收包校验（请求带的 epoch 与当前不符即 `stale`，一个包不发）；
② 结果收集（`apply_effects` 一批**只落一次**，双调会把策略 runs 翻倍）；
③ `upsert_record_if_epoch` 锁内原子写（切库即作废，绝不落进新库）。
"""

from urllib.parse import urlparse

from almond.core import confirm, fetch_hints, fetchpool
from almond.core.domains import get_domain_fetch_config
from almond.core.log import logger
from almond.core.proxy import ensure_working_proxy, resolve_proxy
from almond.core.records import upsert_record_if_epoch
from almond.core.storage import storage_epoch
from almond.web.webui import imgproxy
from almond.web.webui.api_util import require_http_url, send_json


def fetch_one(h, qs: dict, body: dict) -> None:
    """POST /api/fetch：异步提交抓取，立即返回 job_id（结果经 /api/fetch/status 收）。"""
    url = (body.get("url") or "").strip()
    global_proxy = (body.get("proxy") or "").strip() or ""
    err = require_http_url(url)
    if err:
        send_json(h, {"ok": False, "error": err}, 400)
        return

    # ① 请求级纪元校验（与同步版一致）：旧纪元的请求直接丢弃
    start_epoch = storage_epoch()
    req_epoch = body.get("epoch")
    if isinstance(req_epoch, int) and req_epoch != start_epoch:
        logger.info(
            f"丢弃旧存储纪元的抓取请求（epoch {req_epoch} ≠ 当前 {start_epoch}）："
            f"{urlparse(url).hostname}"
        )
        send_json(h, {
            "ok": True, "stale": True, "success": False,
            "error": "存储已切换，旧目录的抓取请求已丢弃",
        })
        return

    proxy = resolve_proxy(url, global_proxy)
    rules = get_domain_fetch_config(url)
    if proxy:
        proxy = ensure_working_proxy(proxy)
    # ② 异步提交：立即返回 job_id，前端通过 /api/fetch/status 轮询结果。
    #    旧版同步版：fetchpool.wait(job) 阻塞请求线程数秒~数分钟，
    #    5 并发 × 长等待 → 浏览器 6 连接全部占满 → 前端所有 GET 排不上队。
    job_id = fetchpool.submit(
        epoch=start_epoch,
        url=url, proxy=proxy,
        selector=rules["detail_selector"] or None,
        cover=rules["cover"],
    )
    payload = {"ok": True, "job_id": job_id}
    # 代理端口已被顶替时，立即告知前端跟上新地址（不用等轮询结果）
    if proxy and proxy != global_proxy:
        payload["proxy_swapped"] = proxy
    send_json(h, payload)


def fetch_status(h, qs: dict, body: dict) -> None:
    """GET /api/fetch/status：异步抓取结果查询（供前端轮询）。

    200：任务完成，返回完整结果（与原 /api/fetch 响应格式一致）；
    204：任务仍在跑，前端稍后再查；
    404：任务不存在（job_id 无效或已被切库清掉）。
    """
    raw_id = (qs.get("job_id") or [""])[0]
    if not raw_id or not raw_id.isdigit():
        send_json(h, {"ok": False, "error": "缺少或无效的 job_id"}, 400)
        return
    job_id = int(raw_id)
    info = fetchpool.peek(job_id)
    if not info["done"]:
        # 还在跑：204 无 body（前端据此继续轮询，不消耗多余带宽）
        h.send_response(204)
        h.end_headers()
        return
    got = info["result"]         # (meta, effects) 或 None（任务被硬停）
    start_epoch = info["epoch"]  # 提交时的存储纪元（fetchpool 代记）
    if storage_epoch() != start_epoch:
        send_json(h, {
            "ok": True, "stale": True, "success": False,
            "error": "存储已切换，抓取结果已丢弃",
        })
        return
    if got is None:
        send_json(h, {
            "ok": True, "stale": True, "success": False,
            "error": "存储已切换，抓取子进程已硬停，结果作废",
        })
        return
    meta, effects = got
    # 效应一批只落一次（曾双调导致 save_strategy 的 runs 双计）
    fetch_hints.apply_effects(effects, epoch=start_epoch)
    if meta is None:
        send_json(h, {"ok": False, "error": "抓取子进程异常，未取得结果"})
        return
    merged = upsert_record_if_epoch(meta, start_epoch)
    if merged is None:
        send_json(h, {
            "ok": True, "stale": True, "success": False,
            "error": "存储已切换，本次抓取结果已丢弃",
        })
        return
    payload = {"ok": True, "record": merged, "success": bool(meta.get("success"))}
    if start_epoch and storage_epoch() != start_epoch:
        payload["stale"] = True
    send_json(h, payload)


def get_img(h, qs: dict, body: dict) -> None:
    """GET /api/img：服务端代抓图片：解析代理 → 交给 imgproxy 取字节 → 写响应。"""
    src = (qs.get("src") or [""])[0]
    global_proxy = (qs.get("proxy") or [""])[0].strip() or ""
    if require_http_url(src):
        h.send_error(400)
        return
    proxy = resolve_proxy(src, global_proxy)
    status, body_bytes, ctype = imgproxy.handle_img(src, proxy)
    imgproxy.send_img(h, body_bytes, ctype, status)


def confirm_stage(h, qs: dict, body: dict) -> None:
    """POST /api/confirm：方案确认阶段（自动抓取的最前面）。

    没有可用方案的域名，各挑一条真实网址跑一次测试抓取，成功学出方案、
    失败写持久结论——前端据此整组放行/跳过。
    """
    domains = body.get("domains")
    if domains is not None and not isinstance(domains, list):
        send_json(h, {"ok": False, "error": "domains 必须是数组"}, 400)
        return
    start_epoch = storage_epoch()
    req_epoch = body.get("epoch")
    if isinstance(req_epoch, int) and req_epoch != start_epoch:
        send_json(h, {"ok": True, "stale": True,
                      "error": "存储已切换，方案确认作废"})
        return
    gp = (body.get("proxy") or "").strip()
    report = confirm.confirm_domains(
        domains=domains,
        proxy_for=lambda u: resolve_proxy(u, gp),
        start_epoch=start_epoch,
    )
    if report.get("aborted") or storage_epoch() != start_epoch:
        send_json(h, {"ok": True, "stale": True,
                      "error": "存储已切换，方案确认作废"})
        return
    send_json(h, {"ok": True, "epoch": storage_epoch(), **report})


ROUTES = {
    ("POST", "/api/fetch"): fetch_one,
    ("GET", "/api/fetch/status"): fetch_status,
    ("GET", "/api/img"): get_img,
    ("POST", "/api/confirm"): confirm_stage,
}
