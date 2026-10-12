"""方案确认模块——自动抓取**最前面**的独立阶段（先确认、后抓取）。

规则（用户定义）：
1. 自动抓取前，每个域名必须先有**最优抓取方案**（活跃策略 / Cookie 直取，
   `fetch_hints.domain_plan()`）；
2. 没有方案的域名 → 从数据库里挑一条该域名的**真实网址**（pending 优先）跑一次
   测试抓取（直连/代理/JS 多路尝试，走 fetchpool 子进程）——成功即学出方案、
   结果顺手入库，写 `confirm(ok=True)`；
3. 测试仍不通 → 写 `confirm(ok=False)`，该域名**整组跳过**，之后不再重测；
4. 已有确认结论（无论成败）→ **不再尝试**：结论里 ok=True 但方案随后失效
   （策略被弃用 / Cookie 被清）同样跳过——「确认过但当前没有方案，就别再试了」。

产出即「系统实测的映射关系与抓取方案」：结论写进 fetch_hints（经 `/api/domains`
的 `tested.{plan, strategy, confirm}` 暴露给前端），前端 `autoFetchEligible`
据此放行/整组跳过。

**显式重测（`force=True`，「刷新」按钮）与自动路径的差异**：自动路径守规则 3/4
（失败不再重试、结论已定不再尝试）——那是防浪费；用户手点「刷新」是显式意志，
两条规则都不适用：绕过结论直接重测，且对没有任何待抓网址的域名（记录都抓过 /
被删光）用**域名根网址** `https://<域名>/` 做只读探测——结论照写（效应 + confirm），
但**不 upsert**：根网址不是用户收藏的链接，不能凭空写进看板。
"""
from concurrent.futures import ThreadPoolExecutor

from almond.core import fetch_hints, fetchpool, records, storage
from almond.core.domains import get_domain_fetch_config, list_domains
from almond.core.hostutil import root_domain
from almond.core.log import logger

_TEST_WORKERS = 8      # 与 fetchpool 子进程池同为 8（前端补抓 5 并发是浏览器侧，不一一对应）
_TEST_REASON = "直连/代理/JS 多路尝试均未取得数据"


def _root(url: str) -> str:
    return root_domain(url) or url


def _candidates(domains: list | None = None) -> tuple[dict, set]:
    """待确认域名 → 待测网址列表（插入顺序）；第二返回值 = 根网址探测域名集。

    - 只看 `fetched=0` 的记录（确认阶段就是为自动抓取服务的）；
    - `auto_fetch=0` 的域名不参与 pending 扫描（用户已明确关掉自动抓取）；
    - 显式传入的 domains（「刷新」按钮）若没有 pending 网址，也保留该名字，并用
      **域名根网址** `https://<域名>/` 做只读探测（计入探测集，`_test_one` 据此
      跳过 upsert）——记录都抓过或被删光时，「重新确认」不该 silently 跳过；
      手点刷新即显式同意为该域名发这一次测试请求，`auto_fetch=0` 不再拦。
    """
    auto = {d["name"]: d.get("auto_fetch") for d in list_domains()}
    wanted = set(domains) if domains else None
    by: dict = {}
    for u in records.list_pending_urls():
        d = _root(u)
        if auto.get(d) is False:
            continue
        if wanted is not None and d not in wanted:
            continue
        by.setdefault(d, []).append(u)
    probes: set = set()
    if wanted:
        for d in wanted:
            by.setdefault(d, [])
            if not by[d]:
                by[d].append(f"https://{d}/")
                probes.add(d)
    return by, probes


def _decide(domain: str, urls: list, force: bool = False) -> tuple:
    """该域名现在该走哪条路：(action, payload)。

    `force=True`（「刷新」按钮的显式重测）绕过「结论已定不再尝试」——规则 4 是
    自动路径的防浪费机制，不该管住用户手动点下的刷新；已有方案的仍走 ready
    （重测一遍纯属浪费请求）。
    """
    if fetch_hints.domain_plan(domain):
        return ("ready", None)                      # 已有方案 → 直接放行
    confirm = fetch_hints.get_confirm(domain)
    if confirm is not None and not force:
        reason = "已确认无可用方案" if not confirm.get("ok") else "确认过但当前无方案"
        return ("skipped", reason)                  # 结论已定 → 不再尝试
    if not urls:
        return ("skipped", "无可用测试网址")
    return ("test", urls[0])


def _test_one(domain: str, url: str, proxy_for, start_epoch: int,
              probe: bool = False) -> tuple:
    """对单个域名的真实网址跑一次完整测试抓取（fetchpool 子进程）。

    返回 (status, info)：status ∈ confirmed / failed / transient / aborted。
    - confirmed：拿到数据 → 方案（策略/Cookie）由效应通道落地，confirm(ok=True)；
    - failed：明确不通 → confirm(ok=False) 持久记录，整组跳过；
    - transient：子进程异常等**不构成「不通」证据**的情况 → 不写结论，下轮再试；
    - aborted：期间切了存储 → 一切作废。

    `probe=True`：根网址只读探测（域名没有待抓记录时「刷新」按钮的兜底）——
    结论照写（效应 + confirm），但**不 upsert**：synthesized 的根网址不是用户
    收藏的链接，不能凭空写进看板。
    """
    rules = get_domain_fetch_config(url)
    proxy = (proxy_for(url) if proxy_for else None) or None
    job = fetchpool.submit(url=url, proxy=proxy, epoch=start_epoch,
                           selector=rules["detail_selector"] or None,
                           cover=rules["cover"])
    try:
        got = fetchpool.wait(job)
    except TimeoutError:
        return ("transient", "测试抓取超时")
    if got is None:
        return ("aborted", "存储已切换")
    meta, effects = got
    if storage.storage_epoch() != start_epoch:
        return ("aborted", "存储已切换")
    fetch_hints.apply_effects(effects, epoch=start_epoch)
    if meta is None:
        return ("transient", "抓取子进程异常")
    ok = bool(meta.get("success"))
    if not probe and records.upsert_record_if_epoch(meta, start_epoch) is None:
        return ("aborted", "存储已切换")
    if ok:
        fetch_hints.confirm_domain(url, True)
        logger.info(f"方案确认：{domain} 可用（{url}）")
        return ("confirmed", None)
    fetch_hints.confirm_domain(url, False, _TEST_REASON)
    logger.info(f"方案确认：{domain} 无可用方案，自动抓取整组跳过（{url}）")
    return ("failed", _TEST_REASON)


def confirm_domains(domains: list | None = None, proxy_for=None,
                    start_epoch: int | None = None, force: bool = False) -> dict:
    """确认一批域名的抓取方案（并发测试，最多 8 路，与 fetchpool 同池）。

    返回 report：{ready, confirmed, failed, skipped, transient, aborted}——
    ready/confirmed 是「有方案、自动抓取放行」；failed/skipped 是「持久结论、
    整组跳过」；transient 是**不构成结论**的意外（超时/子进程异常）——不写
    confirm，前端本轮放弃、之后的轮询再试。

    `force=True`（「刷新」按钮）：绕过「结论已定不再尝试」，且对没有待抓网址的
    域名改用根网址只读探测；自动路径（backfill 调用）不传 force，语义不变。
    """
    if start_epoch is None:
        start_epoch = storage.storage_epoch()
    by, probes = _candidates(domains)
    ready: list = []
    confirmed: list = []
    failed: list = []
    skipped: list = []
    transient: list = []
    aborted = False

    tests: list = []
    for d in sorted(by):
        action, payload = _decide(d, by[d], force)
        if action == "ready":
            ready.append(d)
        elif action == "skipped":
            skipped.append({"domain": d, "reason": payload})
        else:
            tests.append((d, payload))

    if tests:
        with ThreadPoolExecutor(max_workers=min(_TEST_WORKERS, len(tests))) as pool:
            results = list(pool.map(
                lambda t: _test_one(t[0], t[1], proxy_for, start_epoch, t[0] in probes), tests
            ))
        for (d, _url), (status, info) in zip(tests, results):
            if status == "confirmed":
                confirmed.append(d)
            elif status == "failed":
                failed.append({"domain": d, "reason": info})
            elif status == "aborted":
                aborted = True
            else:   # transient：不下持久结论，本轮不算数
                transient.append({"domain": d, "reason": info})

    if ready or confirmed or failed:
        logger.info(
            f"方案确认：就绪 {len(ready)} · 新确认可用 {len(confirmed)} · "
            f"无方案 {len(failed)} · 跳过 {len(skipped)} · 临时失败 {len(transient)}"
        )
    return {"ready": ready, "confirmed": confirmed, "failed": failed,
            "skipped": skipped, "transient": transient, "aborted": aborted}
