"""Scrapling 网页元数据抓取：抓取与解析合并为一步。

选择抓取器拉取页面后，直接在返回的 Response 对象上用 CSS 选择器提取数据。
"""

import time
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher

from almond.core import fetch_hints, hints_cookie, hints_strategy
from almond.core.fields import extract_fields
from almond.core.hostutil import is_domestic
from almond.core.log import logger
from almond.core.proxy import ensure_working_proxy, proxy_reachable

# 详情区块选择器：抓取成功后顺带解析「标签: 值」字段，无匹配时 details 为空列表
DETAIL_SELECTOR = ".space-y-2 > *"

# 国内站点判定见 hostutil.is_domestic（原在此处的 _is_domestic 已迁走）


# 反爬拦截的常见状态码：常规链拿到它们算「被拦」，才触发扩展策略的定制发现
_BLOCK_STATUSES = (401, 403, 429, 503)

# 被拦后的扩展候选（在当前可用路由上按序试）：常规链用的是默认参数，这里换打法。
# 路由切换类策略不在此处——常规链代理/直连互相回退成功时会自动记为策略。
_DISCOVERY_SPECS: list[tuple[str, dict]] = [
    ("static", {"impersonate": "firefox147"}),
    ("static", {"impersonate": "safari184"}),
    ("static", {"impersonate": "chrome124"}),
    ("dynamic", {"real_chrome": True}),
    ("stealth", {"doh": True}),
]


class _StrategyUnavailable(Exception):
    """策略本身的路由当前不可用（如策略要代理、这轮没有代理）——不算策略失败，不弃用。"""


def _sid(route: str, engine: str, params: dict | None = None) -> str:
    """把「路由|引擎|参数…」编码成策略 id：`proxy|static|impersonate=firefox147`。"""
    parts = [route, engine]
    for k, v in (params or {}).items():
        parts.append(k if v is True else f"{k}={v}")
    return "|".join(parts)


def _exec_strategy(url: str, sid: str, timeout: int, proxy: str | None):
    """按策略 id 执行一次抓取。路由由策略决定；缺路由条件抛 _StrategyUnavailable。"""
    parts = sid.split("|")
    route = parts[0]
    engine = parts[1] if len(parts) > 1 else "static"
    params: dict = {}
    for seg in parts[2:]:
        k, eq, v = seg.partition("=")
        params[k] = v if eq else True
    if route == "proxy":
        if not proxy:
            raise _StrategyUnavailable(f"策略需要代理，但本轮没有可用代理")
        p = proxy
    else:
        p = None
    kw: dict = {}
    for k, v in params.items():
        if k == "impersonate":
            kw["impersonate"] = v
        elif k == "real_chrome":
            kw["real_chrome"] = True
        elif k == "doh":
            kw["dns_over_https"] = True
    if engine == "static":
        return Fetcher.get(url, timeout=timeout, proxy=p, **kw)
    if engine == "dynamic":
        return DynamicFetcher.fetch(url, timeout=timeout * 1000, proxy=p, **kw)
    if engine == "stealth":
        return StealthyFetcher.fetch(url, timeout=timeout * 1000, proxy=p, **kw)
    raise ValueError(f"未知策略引擎：{engine}（id={sid}）")   # 旧/坏 id → 当失败弃用


def _fetch_stale(host: str | None) -> bool:
    """抓取期间切换了存储目录 → 立即中止剩余抓取（在每个引擎/策略开始**前**检查）。

    只做检查不杀线程：单次引擎调用内部（如浏览器 20s 超时）无法安全中断，
    但检查点之间的整段策略/三级链/反爬定制不会再烧——切库后后台日志应在
    当前这一次引擎调用结束后停住，而不是继续跑完整条链。
    """
    if fetch_hints.fetch_stale():
        logger.info(f"存储目录已切换，中止后续抓取：{host}")
        return True
    return False


def fetch_page(url: str, timeout: int = 20, proxy: str | None = None):
    """按 Fetcher → DynamicFetcher → StealthyFetcher 降级抓取，成功返回 Response，全部失败返回 None。

    国内网站自动跳过代理直连；外国站配置代理时先用代理抓取，代理不可用则回退直连重试整条降级链。
    经验缓存（fetch_hints）介入：
    - 已知直连不通的域名：没代理 / 代理转发不通 → 直接跳过并打日志，不再烧满三级重试；
    - 代理可用且已知直连不通 → **只跑代理组合**，不再跑直连兜底；
    - 代理端口在但转发不通 → 本轮降级直连（能直连的照跑；不能的上面已拦）。
    **策略层**（可行配方 = 路由+引擎+参数，任何成功都进成绩册，**最快者当选**）：
    1. 有策略 → 先用**成绩册里最快**的那套直接拿数据；成功记本次耗时并刷新使用时间
       （超过 7 天没用会先打「强制复验」日志）；失败/被拦 → 弃用，顺位尝试第二快的
       （每次抓取最多试 3 套已知策略），全部弃完或路由不可用再走常规链；
    2. 常规链成功 → 该组合连同耗时记入成绩册（更快才夺位，慢的当备胎——
       路由切换类的发现也从这里来）；
    3. 常规链拿到 401/403/429/503（被反爬拦）→ 在当前路由上按 _DISCOVERY_SPECS
       定制扩展策略（换指纹/真 Chrome/DoH），首个成功者入库并计时。
    """
    fetch_hints.enter_fetch()   # 绑定存储纪元：抓取期间切库 → 本函数后续所有缓存写入作废
    host = urlparse(url).hostname
    if proxy and is_domestic(url):
        logger.info(f"国内站点，跳过代理直连：{host}")
        proxy = None

    # 代理先做健康顶替：端口开着但转发不通 → 在本机 7889-7899 找可用端口换上，
    # 而不是拿着坏端口往下跑（放最前面，skip_reason 也要按顶替后的地址判定；
    # 顶替动作的日志由 ensure_working_proxy 统一打）
    if proxy:
        proxy = ensure_working_proxy(proxy)
    reason = fetch_hints.skip_reason(url, proxy)
    if reason:
        logger.info(f"跳过抓取：{reason}")
        return None
    if proxy and not proxy_reachable(proxy):
        logger.warning(f"代理 {proxy} 转发不通（区间内无可用顶替端口），本轮降级直连：{host}")
        proxy = None

    # ── 0) Cookie 优先：存过 Cookie 就先带它静态直取一发；
    #        成功即返回（记耗时、会话轮换一并回写），失效/没存则走下面的策略与常规逻辑 ──
    if _fetch_stale(host):
        return None
    ck = hints_cookie.get_cookie(url)
    if ck:
        cookies = hints_cookie.parse_cookie_header(ck["value"])
        if not cookies:
            logger.warning("已存 Cookie 解析不出键值对，弃用")
            hints_cookie.drop_cookie(url)
        else:
            t0 = time.time()
            try:
                # retries=1：Cookie 探测要快失败，重试交给下面的策略/常规链
                response = Fetcher.get(url, timeout=timeout, proxy=proxy,
                                       retries=1, cookies=cookies)
            except Exception as exc:  # noqa: BLE001
                # 网络类异常不是 Cookie 的错（路由/目标抖动）→ 保留 Cookie，回退
                logger.debug(f"Cookie 直取失败（{type(exc).__name__}: {exc}），回退策略/常规链")
            else:
                ms = round((time.time() - t0) * 1000)
                st = getattr(response, "status", None)
                if response is not None and (st is None or st < 400):
                    hints_cookie.touch_cookie(url, ms, response)   # 刷新时间/记耗时/轮换合并
                    if proxy:
                        fetch_hints.record_proxy_success(url)
                    else:
                        fetch_hints.record_direct_success(url)
                    logger.info(f"Cookie 直取成功：{urlparse(url).hostname}（{ms}ms）")
                    return response
                hints_cookie.drop_cookie(url)
                logger.warning(f"Cookie 已失效（HTTP {st}），弃用 → 回退策略/常规链")

    # ── 1) 策略优先：先用成绩册里**最快**的打法；挂了顺位第二快，单轮最多试 3 套 ──
    tried: set = set()
    for _ in range(3):
        if _fetch_stale(host):
            return None
        strat = hints_strategy.get_strategy(url)
        if not strat or strat["id"] in tried:
            break
        sid = strat["id"]
        tried.add(sid)
        if hints_strategy.strategy_stale(url):
            logger.info(f"策略 {sid}（历史最快 {strat.get('best_ms')}ms）距上次成功已超过 "
                        f"{hints_strategy.STRATEGY_STALE_DAYS} 天，强制复验…")
        t0 = time.time()
        try:
            response = _exec_strategy(url, sid, timeout, proxy)
        except _StrategyUnavailable as exc:
            logger.info(f"策略 {sid} 暂不可用（{exc}），走常规链")
            break                       # 路由缺失是环境问题：不弃用，交给常规链兜路由
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"策略 {sid} 执行失败（{type(exc).__name__}: {exc}），弃用并顺位下一套")
            hints_strategy.drop_strategy(url, sid)
            continue
        ms = round((time.time() - t0) * 1000)
        st = getattr(response, "status", None)
        if response is not None and (st is None or st < 400):
            hints_strategy.save_strategy(url, sid, ms)   # 记成绩、刷新时间、最快者当选
            if sid.startswith("proxy|"):
                fetch_hints.record_proxy_success(url)
            else:
                fetch_hints.record_direct_success(url)
            hints_cookie.store_cookie_from_response(url, response)   # 任意成功都顺手收 Cookie
            return response
        logger.warning(f"策略 {sid} 返回 HTTP {st}（{ms}ms），弃用并顺位下一套")
        hints_strategy.drop_strategy(url, sid)

    # ── 2) 常规三级链（代理组合优先，按配置回退直连） ──
    engines = (
        ("static", lambda p: Fetcher.get(url, timeout=timeout, proxy=p)),
        # 浏览器抓取器的 timeout 单位是毫秒，与 Fetcher（秒）不同
        ("dynamic", lambda p: DynamicFetcher.fetch(url, timeout=timeout * 1000, proxy=p)),
        ("stealth", lambda p: StealthyFetcher.fetch(url, timeout=timeout * 1000, proxy=p)),
    )
    proxy_only = bool(proxy) and fetch_hints.proxy_needed(url)
    if proxy_only:
        logger.info(f"经验缓存：{urlparse(url).hostname} 直连不通，仅走代理抓取")
    combos: list[tuple] = [(n, fn, proxy) for n, fn in engines] if proxy else []
    if not proxy_only:
        combos += [(n, fn, None) for n, fn in engines]
    blocked_status: int | None = None
    for i, (ename, engine, p) in enumerate(combos):
        if _fetch_stale(host):
            return None
        if proxy and not proxy_only and i == len(engines):
            logger.info("代理抓取失败，回退直连重试…")
        t0 = time.time()
        try:
            response = engine(p)
        except Exception as exc:  # 网络错误、代理/浏览器不可用、反爬拦截等都走降级
            if p is None and fetch_hints.is_network_error(str(exc)):
                fetch_hints.record_direct_failure(url)   # 直连网络不可达 → 记入经验
            via = f"（代理 {p}）" if p else ""
            logger.debug(f"抓取失败{via}（{type(exc).__name__}: {exc}），尝试下一抓取器…")
            continue
        ms = round((time.time() - t0) * 1000)
        status = getattr(response, "status", None)
        # 收到 HTTP 响应（任何状态码）= 这条链路连得上远程，回写经验
        if p is None:
            fetch_hints.record_direct_success(url)
        else:
            fetch_hints.record_proxy_success(url)
        if status in _BLOCK_STATUSES:
            blocked_status = status
        if response is not None and (status is None or status < 400):
            # 成功 → 这套配方连耗时进成绩册（比当前最快才夺位，慢的当备胎）
            hints_strategy.save_strategy(url, _sid("proxy" if p else "direct", ename), ms)
            hints_cookie.store_cookie_from_response(url, response)   # 任意成功都顺手收 Cookie
            return response
        logger.debug(f"抓取返回 HTTP {status}，尝试下一抓取器…")

    # ── 3) 被反爬拦掉（拿到过 401/403/429/503）→ 在当前路由上定制扩展策略 ──
    if blocked_status is not None:
        route = "proxy" if proxy else "direct"
        logger.warning(f"常规链被反爬拦截（HTTP {blocked_status}），定制可用策略（路由 {route}）…")
        for engine, params in _DISCOVERY_SPECS:
            if _fetch_stale(host):
                return None
            sid = _sid(route, engine, params)
            t0 = time.time()
            try:
                response = _exec_strategy(url, sid, timeout, proxy)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"候选 {sid}：{type(exc).__name__}: {exc}")
                continue
            ms = round((time.time() - t0) * 1000)
            st = getattr(response, "status", None)
            if response is not None and (st is None or st < 400):
                hints_strategy.save_strategy(url, sid, ms)
                if route == "proxy":
                    fetch_hints.record_proxy_success(url)
                else:
                    fetch_hints.record_direct_success(url)
                hints_cookie.store_cookie_from_response(url, response)
                logger.info(f"已定制可行策略：{sid}（{ms}ms，下次直接用）")
                return response
            logger.debug(f"候选 {sid} → HTTP {st}")
        logger.warning("扩展候选全部被拦，本轮放弃（下次触发时重新定制）")
    return None


def extract_metadata(response, base_url: str) -> dict:
    """在 Response 上用 CSS 选择器按优先级提取元数据，相对路径用 urljoin 补全。"""

    def attr(selector: str) -> str:
        return response.css(selector).extract_first() or ""

    title = (
        attr('meta[property="og:title"]::attr(content)')
        or attr('meta[name="twitter:title"]::attr(content)')
    )
    if not title:
        title_elements = response.css("title")
        if title_elements:
            title = title_elements[0].text.strip()

    thumbnail = attr('meta[property="og:image"]::attr(content)')
    favicon = attr('link[rel*="icon"]::attr(href)')

    return {
        "title": title.strip() if title else "",
        "thumbnail": urljoin(base_url, thumbnail) if thumbnail else "",
        "favicon": urljoin(base_url, favicon) if favicon else "",
    }


def get_metadata(url: str, timeout: int = 20, proxy: str | None = None,
                 selector: str | None = None, cover: bool | None = None) -> dict:
    """抓取一个 URL 的元数据；全部抓取失败时仅保留 URL，标题留空供手动补充。

    selector：按站点覆盖详情选择器（空/None 用默认 DETAIL_SELECTOR）——
      域名级配置（domains.detail_selector）由调用方传进来，选择器写错只会让详情为空。
    cover：按域名的封面开关——False=不要封面，True=只要图标（拿 favicon 当封面），
      None=默认走 og:image。
    """
    meta = {"url": url, "title": "", "thumbnail": "", "favicon": "", "success": False, "details": []}
    response = fetch_page(url, timeout=timeout, proxy=proxy)
    if response is None:
        return meta
    base_url = getattr(response, "url", None) or url
    meta.update(extract_metadata(response, base_url))
    try:
        meta["details"] = extract_fields(response.css(selector or DETAIL_SELECTOR))
    except Exception:  # 解析任意 HTML 时个别页面可能出错（含非法选择器），不影响主元数据
        meta["details"] = []
    if cover is False:
        meta["thumbnail"] = ""
    elif cover is True and meta.get("favicon"):
        meta["thumbnail"] = meta["favicon"]
    meta["success"] = True
    return meta
