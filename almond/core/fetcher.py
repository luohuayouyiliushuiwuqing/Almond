"""Scrapling 网页元数据抓取：抓取与解析合并为一步。

选择抓取器拉取页面后，直接在返回的 Response 对象上用 CSS 选择器提取数据。
"""

import time
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher

from almond.core import fetch_hints, hints_cookie, hints_strategy
from almond.core.fields import extract_fields, extract_tags
from almond.core.hostutil import is_domestic
from almond.core.log import logger
from almond.core.proxy import ensure_working_proxy, proxy_reachable
from almond.core.urlguard import require_public_url

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


def fetch_page(url: str, timeout: int = 20, proxy: str | None = None,
               force_route: str | None = None, static_only: bool = False):
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

    `force_route`（`proxy.forced_route` 的结论，`"proxy"`/`"direct"`/None）=
    用户**显式规则**指定的路由。全局默认值只是「没表态时的办法」，学习层可以盖过；
    显式规则是明确意志，学习层不许盖过：
    - 路线不符的活跃策略本轮**不用**（不弃用——没被证伪，只是不合当前配置），
      直接交常规链按显式路由抓；
    - `"proxy"` → 常规链不再回退直连（`proxy_only`，与「已知直连不通」同款）；
    - `"proxy"` 而调用方根本没给代理地址（全局代理未配置/被清空）→ 本轮不抓取
      （与 `skip_reason` 同哲学：该走的路不在，不烧引擎，更不 silently 直连）；
    - `"direct"` → 代理参数作废（「规则命中即为强制值」，不再向下兜底）。
    国内旁路优先于一切代理规则（`is_domestic`）：国内站就算 force_route="proxy"
    也直连——与 `resolve_proxy` 的结论保持一致。
    """
    # 兜底闸门：/api/fetch 与 /api/img 在 webui 层就拦过一道，但 confirm 阶段的测试
    # 抓取是从库里取历史网址直接投递子进程的，不经过那两个入口 —— 政策必须落在
    # 这张唯一漏斗上，否则「入库时没人拦、抓取时才拦」的洞仍在。
    blocked = require_public_url(url)
    if blocked:
        logger.warning(f"目标被安全策略拦下，不发起抓取：{blocked}")
        return None
    fetch_hints.enter_fetch()   # 绑定存储纪元：抓取期间切库 → 本函数后续所有缓存写入作废
    host = urlparse(url).hostname
    if force_route == "direct":
        proxy = None                # 显式直连：代理参数作废（与 resolve_proxy 同结论）
    elif proxy and is_domestic(url):
        logger.info(f"国内站点，跳过代理直连：{host}")
        proxy = None

    # 代理先做健康顶替：端口开着但转发不通 → 在本机 7889-7899 找可用端口换上，
    # 而不是拿着坏端口往下跑（放最前面，skip_reason 也要按顶替后的地址判定；
    # 顶替动作的日志由 ensure_working_proxy 统一打）
    had_proxy = bool(proxy)         # 调用方给了代理地址（显式规则或全局默认）——记下来
    if proxy:
        proxy = ensure_working_proxy(proxy)
    reason = fetch_hints.skip_reason(url, proxy)
    if reason:
        logger.info(f"跳过抓取：{reason}")
        return None
    if proxy and not proxy_reachable(proxy):
        logger.warning(f"代理 {proxy} 转发不通（区间内无可用顶替端口），本轮降级直连：{host}")
        proxy = None
    # 显式规则要求走代理、调用方却根本没给代理地址 → 不抓：silently 直连正是
    # 「规则形同虚设」的来源（用户指定了用代理，实际全走直连）。给了地址但转发
    # 不通的仍按上文降级直连（环境性故障，与全局默认路同一兜底哲学）。
    # 国内站不在此列——国内旁路优先于代理规则，此时 proxy=None 是旁路的果。
    if force_route == "proxy" and not had_proxy and not is_domestic(url):
        logger.info(f"显式规则要求走代理，但未配置代理地址，本轮不抓取：{host}")
        return None

    # ── 0.5) 静态专用（OG 快速模式）：只发 Fetcher.get，绝不起浏览器、
    #         不预带/不写 Cookie 缓存。标题与封面都在静态 HTML 的 <head> 里，
    #         浏览器渲染纯属浪费；任意失败直接返回 None（OG 卡片接受无数据，
    #         不为它烧 DynamicFetcher/StealthyFetcher）。对所有站点通用，不含
    #         任何域名特化（YouTube 的 og 标签同样在静态 HTML 里，自然受益）。
    if static_only:
        combos = []
        if proxy:
            combos.append(proxy)
        combos.append(None)
        for p in combos:
            if _fetch_stale(host):
                return None
            t0 = time.time()
            try:
                response = Fetcher.get(url, timeout=timeout, proxy=p)
            except Exception as exc:  # noqa: BLE001
                via = f"（代理 {p}）" if p else ""
                logger.debug(f"OG 静态抓取失败{via}（{type(exc).__name__}: {exc}）")
                continue
            ms = round((time.time() - t0) * 1000)
            status = getattr(response, "status", None)
            if p is None:
                fetch_hints.record_direct_success(url)
            else:
                fetch_hints.record_proxy_success(url)
            if response is not None and (status is None or status < 400):
                logger.info(f"OG 静态抓取成功：{urlparse(url).hostname}（{ms}ms）")
                return response
            logger.debug(f"OG 静态抓取返回 HTTP {status}")
        return None

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
        # 显式规则指定的路由与策略路线不符 → 本轮不用它：**不弃用**（没被证伪，
        # 只是不合当前配置，用户取消指定后它仍是最快方案），也不必顺位（成绩册
        # 只暴露活跃位）——直接交常规链按显式路由抓。没有这一条，学过的
        # direct|static 会永远盖过用户「用代理」的指定（github.com 的直连逻辑）。
        if force_route and not sid.startswith(force_route + "|"):
            logger.info(f"策略 {sid} 与显式路由 {force_route} 不符，本轮不用，走常规链")
            break
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
    # 「已知直连不通」与「显式规则要求走代理」都只跑代理组合，不跑直连兜底
    # （前者是经验结论，后者是用户意志——「规则命中即为强制值」）
    proxy_only = bool(proxy) and (
        fetch_hints.proxy_needed(url) or force_route == "proxy"
    )
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
                 selector: str | None = None, cover: bool | None = None,
                 force_route: str | None = None, detail_mode: str | None = None,
                 static_only: bool = False) -> dict:
    """抓取一个 URL 的元数据；全部抓取失败时仅保留 URL，标题留空供手动补充。

    selector：按站点覆盖详情选择器（空/None 用默认 DETAIL_SELECTOR）——
      域名级配置（domains.detail_selector）由调用方传进来，选择器写错只会让详情为空。
    cover：按域名的封面开关——False=不要封面，True=只要图标（拿 favicon 当封面），
      None=默认走 og:image。
    force_route：用户显式规则指定的路由（`proxy.forced_route` 的结论），
      见 `fetch_page`——约束策略层与常规链，默认 None（学习层自由发挥）。
    detail_mode：按域名的详情提取形态（domains.detail_mode）——
      None/""=「标签: 值」行（默认，selector 留空用 DETAIL_SELECTOR）；
      "tags"=扁平标签链（extract_tags：**每个匹配元素本身就是一个标签**，
      如 B 站 .tag-panel .tag-link）。tags 模式**没有默认选择器**：留空一律
      详情为空——绝不拿 .space-y-2 > * 的行结构硬套标签链。
      "og"=OG 快速模式：纯静态抓取（fetch_page(static_only=True)，不起浏览器、
      不预带 Cookie）、只解析 <head> 里的 og:title/og:image，并跳过正文详情
      解析。**通用、不含任何域名特化**——任何把 og 标签写在静态 HTML 里的站点
      （含 YouTube）都受益，且比「整页+浏览器」明显更快。
    static_only：强制纯静态抓取（通常由 detail_mode="og" 内部置位，也可由调用方直
      接传，用于只需标题/封面的轻量场景）。
    """
    meta = {"url": url, "title": "", "thumbnail": "", "favicon": "", "success": False, "details": []}
    # OG 快速模式 → 强制纯静态抓取（不起浏览器、不预带/不写 Cookie）
    if detail_mode == "og":
        static_only = True
    response = fetch_page(url, timeout=timeout, proxy=proxy,
                          force_route=force_route, static_only=static_only)
    if response is None:
        return meta
    base_url = getattr(response, "url", None) or url
    meta.update(extract_metadata(response, base_url))
    # OG 模式只抓标题+封面，正文详情一律不解析（浏览器之外省下的第二笔开销）
    if detail_mode != "og":
        try:
            if detail_mode == "tags":
                meta["details"] = extract_tags(response.css(selector), base_url) if selector else []
            else:
                meta["details"] = extract_fields(response.css(selector or DETAIL_SELECTOR))
        except Exception:  # 解析任意 HTML 时个别页面可能出错（含非法选择器），不影响主元数据
            meta["details"] = []
    if cover is False:
        meta["thumbnail"] = ""
    elif cover is True and meta.get("favicon"):
        meta["thumbnail"] = meta["favicon"]
    meta["success"] = True
    return meta
