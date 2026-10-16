"""代理归一模块：规则匹配 + 代理判定 + 转发健康 + 本机端口顶替。

「要不要走代理」「地址用哪个」「这个地址能不能转发」全在这里回答，
是全项目唯一的代理出入口（`resolve_proxy` / `proxy_reachable` / `ensure_working_proxy`）。
原先判定逻辑散在 server.effective_proxy、fetch_hints 健康层、records 规则匹配三处。

本机端口探测：7889-7899 并行 TCP 扫描（`scan_open_ports`）——
端口开着 ≠ 代理可用，可用性结论以**转发验证**为准：国内站点仅作诊断（能否 GET 到 200），
国外可达性用 **GFW 阻断站点硬验证**（默认 google.com / youtube.com，urllib 默认严格校验 TLS
证书）——端口开着但代理未真正转发时，请求会被 GFW 重置 / 代理返回无效证书 → 异常 → 判不通，
从而根除「端口开着 / 被劫持错误页回显域名」造成的假阳性。（IP 回显类服务在国内常能直连到达，
故不作为可用硬标准。）
"""

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatchcase
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

from almond.core import domains, records
from almond.core.hostutil import is_domestic
from almond.core.log import logger

# 待探测端口：7889 ~ 7899（含两端）
PROXY_PORT_RANGE = range(7889, 7900)
_PROBE_TIMEOUT = 0.25  # 单端口 TCP 超时；并行执行，总耗时约等于单次超时

# 转发健康缓存与探测（区别于 TCP 扫描：这里证明「能连到远程」）
_PROXY_TTL_OK = 30      # 代理健康结果缓存：通过后 30s 内不再探测
_PROXY_TTL_FAIL = 15    # 失败也缓存，避免一轮补抓每条都先花数秒探测
_PROXY_PROBE_TIMEOUT = 3.0

# 国内目标（仅诊断）：国内站点本就直连旁路，不卡可用性；只测「能不能 GET 到 2xx」。
_DOMESTIC_TARGETS = [
    ("baidu", "https://www.baidu.com"),
    ("bilibili", "https://www.bilibili.com"),
]

# 国外可达性的**硬标准**：经代理拉 GFW 阻断站点（默认 google.com / youtube.com）。
# 这类站点在国内「没代理就打不开」；urllib 默认做严格 TLS 证书校验，因此端口开着
# 但代理未真正转发时，请求会被 GFW 重置 / 代理返回无效证书 → 异常 → 判不通，
# 绝不会被「端口在 / 错误页回显域名」误导。首个成功即停。
_FOREIGN_TARGETS = [
    ("google", "https://www.google.com"),
    ("youtube", "https://www.youtube.com"),
]
_LAST_CONN_FP = None    # 连通性画像指纹：30s 刷新时画像不变则不重复打矩阵日志

_LOCK = threading.RLock()                                   # 护 _HEALTH / _ENSURE
_PROBE_LOCK = threading.Lock()                              # 只串行化网络探测本身
_HEALTH: dict[str, tuple[dict, float]] = {}                 # 代理地址 → (连通性字典, 探测时刻)
_ENSURE: dict | None = None                                 # {"in": 输入地址, "out": 顶替结果, "at": 时刻}
_DETECT_TIMEOUT = 3.0     # 端口逐个验证转发的单端口超时（端口间并行，总耗时≈单端口）
_ENSURE_TTL = 20           # 「坏端口 → 顶替结论」缓存：一条补抓轮里只做一次完整探测


# ---------------------------------------------------------------- 端口扫描（TCP 层）

def _tcp_probe(port: int) -> int | None:
    """端口能建立 TCP 连接则返回端口号，否则返回 None。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_PROBE_TIMEOUT):
            return port
    except OSError:
        return None


def scan_open_ports(host: str = "127.0.0.1") -> list[int]:
    """并行 TCP 探测区间内**端口开着**的端口，按升序返回。

    端口开着 ≠ 代理可用（端口在但上游挂了照样转发不通）——需要可用性结论时用
    `detect_working_proxy()`（在本函数结果上再做真实转发验证）。
    """
    with ThreadPoolExecutor(max_workers=len(PROXY_PORT_RANGE)) as pool:
        results = list(pool.map(_tcp_probe, PROXY_PORT_RANGE))
    return [p for p in results if p is not None]


def _reachable(proxy: str, url: str, timeout: float) -> bool:
    """经代理能否 GET 到该站点（2xx/3xx 即算可达）。用于国内诊断，不卡可用性。"""
    try:
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
        resp = opener.open(url, timeout=timeout)
        return 200 <= resp.status < 400
    except Exception:        # noqa: BLE001 — 隧道失败/超时：算不可达
        return False


def _foreign_results(proxy: str, timeout: float) -> dict:
    """经代理拉 GFW 阻断站点：返回 {服务名: 是否 2xx 且 TLS 证书校验通过}。

    urllib 默认校验目标证书——代理若未真正转发（GFW 重置 / 返回无效证书的错误页），
    会抛连接或 SSL 异常被捕获为 False；只有真能到达真实站点才判通。首个成功即停。
    """
    opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
    res: dict = {}
    for svc, url in _FOREIGN_TARGETS:
        ok = False
        try:
            resp = opener.open(url, timeout=timeout)
            ok = 200 <= resp.status < 400
        except Exception:        # noqa: BLE001 — 连接失败 / SSL 校验不过 / 超时 均算不通
            ok = False
        res[svc] = ok
        if ok:
            break
    return res


def _probe_connectivity(proxy: str, timeout: float) -> dict:
    """经代理分别探国内 / 国外连通性。

    返回 {domestic:{...}, foreign:{...}}：
    - domestic：仅诊断（能否 GET 到 2xx），国内站本就直连旁路不卡它；
    - foreign：各 GFW 阻断站点是否经代理 2xx 可达（硬标准）。
    """
    out: dict = {"domestic": {}, "foreign": {}}
    for key, url in _DOMESTIC_TARGETS:
        out["domestic"][key] = _reachable(proxy, url, timeout)
    out["foreign"] = _foreign_results(proxy, timeout)
    return out


def _is_usable(conn: dict) -> bool:
    """代理是否可用：以「能连上国外（任一 GFW 阻断站点经代理 2xx 可达）」为硬标准。"""
    return any(conn.get("foreign", {}).values())


def _forward_probe(proxy: str, timeout: float) -> dict:
    """转发验证：返回连通性字典（兼容旧调用点，等价 _probe_connectivity）。"""
    return _probe_connectivity(proxy, timeout)


def proxy_reachable(proxy: str, timeout: float = _PROXY_PROBE_TIMEOUT) -> bool:
    """代理转发是否可用（带 TTL 缓存：通过 30s / 失败 15s）。

    区别于端口探测：端口在 ≠ 能连到远程。这里分国内 / 国外探真实站点，
    以「能连上国外（google/youtube 等 GFW 阻断站点经代理 2xx 可达）」为可用硬标准；
    连通性画像由 `detect_working_proxy` 打到日志。
    **探测绝不持有 `_LOCK`**——probe 跑在锁内时一次最多数秒，期间所有共享状态
    访问全部排队；现在只用独立的 `_PROBE_LOCK` 串行化探测本身。
    """
    now = time.time()
    with _LOCK:
        hit = _HEALTH.get(proxy)
        if hit is not None and now - hit[1] < (_PROXY_TTL_OK if _is_usable(hit[0]) else _PROXY_TTL_FAIL):
            return _is_usable(hit[0])
    with _PROBE_LOCK:
        with _LOCK:                       # 双检：等锁期间可能已被别人探好
            hit = _HEALTH.get(proxy)
            if hit is not None and now - hit[1] < (_PROXY_TTL_OK if _is_usable(hit[0]) else _PROXY_TTL_FAIL):
                return _is_usable(hit[0])
        conn = _forward_probe(proxy, timeout)   # ← 网络 I/O：锁外进行
        with _LOCK:
            _HEALTH[proxy] = (conn, time.time())
    return _is_usable(conn)


# ---------------------------------------------------------------- 本机端口顶替

def detect_working_proxy(host: str = "127.0.0.1") -> str | None:
    """在 7889-7899 里找一个**转发真正可用**的本机代理端口（编号最小者）。

    两步：① TCP 并行探开（`scan_open_ports`，约 0.25s）；② 对开着的端口**并行**做
    真实转发验证——分国内（baidu/bilibili）与国外（google/instagram）两组探真实站点，
    以「能连上国外」为可用硬标准。只做 ① 的话，端口开着但上游挂了的坏端口会被当成
    可用填回去——正是「代理不好使还一直用旧端口」的来源。
    每轮把各端口连通性矩阵打到日志（仅连通性画像变化时），方便排查。
    """
    global _LAST_CONN_FP
    ports = scan_open_ports(host)
    if not ports:
        return None
    urls = [f"http://{host}:{p}" for p in ports]
    # 直接并行 _forward_probe（探测不在锁内，池化不会互相卡），结果回填缓存
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        conns = list(pool.map(lambda u: _forward_probe(u, _DETECT_TIMEOUT), urls))
    now = time.time()
    with _LOCK:
        for u, conn in zip(urls, conns):
            _HEALTH[u] = (conn, now)
    # 连通性矩阵日志（画像不变则不刷屏）
    fp = tuple(
        (p, tuple(conns[i]["domestic"].values()), tuple(conns[i]["foreign"].values()))
        for i, p in enumerate(ports)
    )
    if fp != _LAST_CONN_FP:
        _LAST_CONN_FP = fp
        bits = []
        for i, p in enumerate(ports):
            d, f = conns[i]["domestic"], conns[i]["foreign"]
            dom = "".join("✓" if d[k] else "✗" for k in d)
            frn = "".join("✓" if f[k] else "✗" for k in f)
            bits.append(f"{p}[国内{dom} 国外{frn}]")
        chosen = next((p for p, c in zip(ports, conns) if _is_usable(c)), None)
        verdict = f"http://{host}:{chosen}" if chosen else "无可用代理（国外均不通）"
        logger.info(f"代理连通性探测：{' · '.join(bits)} → 选用 {verdict}")
    for p, conn in zip(ports, conns):
        if _is_usable(conn):
            if p != ports[0]:
                logger.debug(f"端口 {ports[0]} 等转发不通，跳过，选用 {host}:{p}")
            return f"http://{host}:{p}"
    return None


def _is_loopback(proxy: str) -> bool:
    try:
        return urlparse(proxy).hostname in ("127.0.0.1", "localhost", "::1")
    except ValueError:
        return False


def ensure_working_proxy(proxy: str | None) -> str | None:
    """代理转发不通时，尝试在本机 7889-7899 找一个**转发可用**的新端口顶替。

    - 传 None / 空 → 原样返回（没配代理不归这里管）；
    - 当前地址健康 → 原样返回（健康结论走 proxy_reachable 的 30s/15s 缓存）；
    - 当前地址是**本机回环**且转发不通 → 顶替成探测到的可用端口（找不到就保持原地址，
      由 fetch_page 照旧降级直连）；远程地址不做本地端口猜测；
    - 结论缓存 `_ENSURE_TTL` 秒：一轮补抓里不会每条记录都重跑完整探测。
    """
    global _ENSURE
    if not proxy:
        return proxy
    now = time.time()
    with _LOCK:
        cached = _ENSURE
        if cached and cached["in"] == proxy and now - cached["at"] < _ENSURE_TTL:
            return cached["out"]
    if proxy_reachable(proxy):
        out = proxy
    elif not _is_loopback(proxy):
        out = proxy                      # 远程代理：本机端口区间与它无关，不瞎换
    else:
        alt = detect_working_proxy()
        out = alt if (alt and alt != proxy) else proxy
        if out != proxy:
            logger.info(f"代理 {proxy} 转发不通，已自动顶替为可用端口 {out}")
    with _LOCK:
        _ENSURE = {"in": proxy, "out": out, "at": time.time()}
    return out


# ---------------------------------------------------------------- 定时刷新

REFRESH_INTERVAL = 30           # 后台线程重探本机代理端口的间隔（秒）
_CURRENT: dict | None = None    # 最近一次探测结论：{"proxy": str | None, "at": float}


def current_proxy() -> str | None:
    """后台线程最近一次探到的可用代理（None = 区间内没有 / 还没探过）。

    只读内存结论、**不做任何网络探测**——供轮询类调用方（GET /api/proxy-current）
    廉价跟随；要主动探测请走 `detect_working_proxy()`。
    """
    with _LOCK:
        cur = _CURRENT
    return cur["proxy"] if cur else None


def refresh_proxy() -> str | None:
    """重探一次本机代理端口，更新「当前代理」结论；结论变化时打一条 info。

    探测本体就是 `detect_working_proxy()`（含转发验证，结论顺带写进 `_HEALTH`），
    与启动时的一次性探测完全同一条路径。首轮必记日志（接原先启动日志的位置），
    之后**只在结论变化时记**——每 30s 一轮，不变化不打，避免日志刷屏。
    """
    global _CURRENT
    found = detect_working_proxy()
    with _LOCK:
        first = _CURRENT is None
        prev = _CURRENT["proxy"] if _CURRENT else None
        _CURRENT = {"proxy": found, "at": time.time()}
    if first:
        logger.info(f"代理探测（7889-7899）：{found or '未发现本地代理'}")
    elif found != prev:
        logger.info(f"本机代理已自动更新：{prev or '无'} → {found or '未发现'}")
    return found


def start_proxy_refresh(interval: int = REFRESH_INTERVAL) -> threading.Thread:
    """启动后台守护线程：每 `interval` 秒重探一次本机代理端口（首轮立即执行）。

    线程只更新内存结论与日志，不写库、不改任何配置；没探到可用端口同样记账，
    下一轮再试（「尝试更新」——失败不是终点）。daemon=True：随主进程退出。
    只在服务启动入口调用；子进程（fetchpool spawn）不会重入，无双开风险。
    """
    def _loop() -> None:
        while True:
            try:
                refresh_proxy()
            except Exception:               # noqa: BLE001 — 定时任务不许死
                logger.exception("代理定时刷新出错，下轮重试")
            time.sleep(interval)

    t = threading.Thread(target=_loop, name="proxy-refresh", daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------- 规则匹配

def _pattern_matches(pattern: str, host: str, path: str) -> bool:
    """通配符匹配：含 / 时连路径一起匹配，否则只匹配主机名。"""
    target = f"{host}{path}" if "/" in pattern else host
    return fnmatchcase(target.lower(), pattern.lower())


def match_rule(url: str, rules: list[dict] | None = None) -> tuple[bool, bool]:
    """按 URL 模式匹配代理规则。

    返回 (是否命中, 是否要走代理)。未命中返回 (False, False)，
    交由域名规则 / 全局代理兜底。
    规则按模式长度降序排列，更长（更具体）的先匹配（`list_proxy_rules` 的序）。
    `rules`：可预取整批规则复用（批量判定时省去反复开库）。
    """
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        path = p.path or "/"
    except Exception:
        return False, False
    if not host:
        return False, False
    if rules is None:
        rules = records.list_proxy_rules()
    for rule in rules:
        if _pattern_matches(rule["pattern"], host, path):
            return True, bool(rule["need_proxy"])
    return False, False


# ---------------------------------------------------------------- 代理判定

def effective_proxy(url: str, global_proxy: str) -> str | None:
    """代理优先级：URL 模式规则 > 域名规则 > 全局代理默认值。

    映射只表达「要不要走代理」，实际地址统一取 global_proxy。
    规则命中即为强制值——need_proxy=0 表示强制直连，不再向下兜底。
    """
    hit, need = match_rule(url)
    if hit:
        return (global_proxy or None) if need else None
    domain_need = domains.get_domain_need_proxy(url)
    if domain_need is not None:
        return (global_proxy or None) if domain_need else None
    return global_proxy or None


def resolve_proxy(url: str, global_proxy: str) -> str | None:
    """**全项目唯一代理判定入口**：优先级判定 + 国内站点旁路直连。

    国内站点就算规则说用代理也直连（fetch_page 对直调方另有同款旁路兜底）。
    """
    proxy = effective_proxy(url, global_proxy)
    if proxy and is_domestic(url):
        return None
    return proxy


def forced_route(url: str) -> str | None:
    """用户**显式规则**指定的路由（`"proxy"` / `"direct"`）；没有显式规则返回 None。

    与 `effective_proxy` 同源同序（URL 模式规则 > 域名规则），但**不含全局默认值**：
    全局默认只是「用户没表态时的办法」，学习层（策略成绩册的最快者当选）可以盖过它；
    显式规则是用户的明确意志，学习层不许盖过——`fetch_page(force_route=…)` 据此
    过滤策略、强制代理时不再回退直连。国内旁路不在这里（它在 `resolve_proxy`，
    国内站就算规则说用代理也直连）。
    """
    hit, need = match_rule(url)
    if hit:
        return "proxy" if need else "direct"
    domain_need = domains.get_domain_need_proxy(url)
    if domain_need is not None:
        return "proxy" if domain_need else "direct"
    return None


# ---------------------------------------------------------------- 侧栏徽标

def list_proxy_domains() -> list[str]:
    """最终会走代理的域名（供侧边栏标记）。

    判定优先级与 `resolve_proxy` 一致（不含国内旁路——徽标只回答「规则上要不要」）：
    URL 模式规则命中 → 规则说了算；否则看域名规则是否为「用代理」。
    候选 = domains 表 ∪ 全部记录按根域名去重（`records.proxy_decision_inputs` 单连接取数）。
    """
    inputs = records.proxy_decision_inputs()
    names, rules, domain_need = inputs["names"], inputs["rules"], inputs["domain_need"]
    proxied = []
    for name in sorted(names):
        # 侧边栏是根域名，而 *.example.com 不匹配顶级域名本身：
        # 根域名与 www. 子域名各探测一次，任一命中即代表该域名下的记录
        hit, need = False, False
        for host in (name, f"www.{name}"):
            for rule in rules:
                if _pattern_matches(rule["pattern"], host, "/"):
                    hit, need = True, bool(rule["need_proxy"])
                    break
            if hit:
                break
        if hit:
            if need:
                proxied.append(name)
        elif domain_need.get(name):
            # SQLite 返回整数 1/0/NULL：真值判断即可（1=用代理，0/NULL 不标）
            proxied.append(name)
    return proxied
