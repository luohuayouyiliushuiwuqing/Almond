"""抓取经验缓存：可达性结论 + 效应通道 + 存储纪元门 + 方案确认字段。

本模块是策略/Cookie 两个兄弟模块（hints_strategy / hints_cookie）的**公共底座**：
JSON 缓存存取（`load`/`save`）、跨进程效应通道（子进程收集、主进程落地）、
存储纪元写守卫、根域名键（`host_of`）都住在这里；成绩册与 Cookie 的数据语义
分别在两个兄弟模块里。为避免 import 环，兄弟模块只依赖本节公开原语，
本模块的 `apply_effects` 反向调用它们时用**函数内延迟 import**。

经验缓存解决三类时间浪费：
1. 国外站直连必死（超时/被重置），三级抓取器 × scrapling 内部重试 × 超时，
   一条能烧一两分钟——跑过一次就该记住，下次没代理直接跳过。
2. 代理端口在、但转发不通——健康判定见 `proxy.proxy_reachable`（不在本模块）。
3. 常规打法拿到了数据 → 学成策略（hints_strategy）；任何成功顺手收 Cookie（hints_cookie）。

数据放**存储目录的 cache/ 下**（`cache/fetch_hints.json`，JSON 缓存而非关键数据）：
**切换存储目录不迁移本文件**——新目录从零开始，旧经验留在旧目录；删掉文件即重置全部学习。
按**根域名**记录：`direct`/`proxy`（三态可达性）+ `strategy` + `cookie` + `confirm`。

线程安全：模块级 RLock，读写文件原子替换（ThreadingHTTPServer + 补抓 5 并发）。
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from almond.core import hostutil, storage
from almond.core.log import logger
from almond.core.proxy import proxy_reachable

HINTS_FILENAME = "fetch_hints.json"
TS_FMT = "%Y-%m-%d %H:%M:%S"     # 全部经验时间戳格式（策略/Cookie 模块共用）

# 直连「网络不可达」类错误的关键词（大小写不敏感）。
# 命中才记 direct=False——403/404/反爬拦截说明**连得上**，不能算直连不通。
_NETWORK_MARKERS = (
    "timed out", "timeout", "connection reset", "reset by peer",
    "connection refused", "failed to connect", "connection failed",
    "could not resolve", "cannot resolve", "resolve host", "name_not_resolved",
    "recv failure", "empty reply", "no route", "network is unreachable",
    "connection aborted", "connection closed",
    "tunnel connection failed", "err_connection", "err_tunnel", "err_proxy",
    "proxyconnect", "socks",
)

LOCK = threading.RLock()                                        # 护 _DATA（策略/Cookie 共用）
_DATA: dict | None = None                                       # {"hosts": {域名: 经验行}}

# 存储纪元写守卫：fetch_page 入口 enter_fetch() 绑定当前纪元；抓取期间切换了存储目录
# → 后续写入（经验/策略/Cookie）全部静默作废，绝不把旧项目的学习写进新目录。
_FETCH_EPOCH = threading.local()


def enter_fetch() -> int:
    """fetch_page 入口调用：绑定本次抓取所属的存储纪元。

    同时丢弃内存缓存——抓取子进程是常驻的，别的进程（主进程/兄弟子进程）
    写过的新策略/Cookie 必须在下一次抓取时读到盘上的最新版本。
    """
    global _DATA
    with LOCK:
        _DATA = None
    epoch = storage.storage_epoch()
    _FETCH_EPOCH.epoch = epoch
    return epoch


def write_allowed() -> bool:
    """未绑定纪元（脚本直接调 record_* 等）或纪元仍一致 → 允许写。"""
    epoch = getattr(_FETCH_EPOCH, "epoch", None)
    return epoch is None or epoch == storage.storage_epoch()


# ---------------------------------------------------------------- 效应收集（子进程模式）
# 抓取跑在独立子进程里（almond.core.fetchpool）——子进程**不直接写缓存文件**：
# 跨进程没有这把 RLock，各写各的会互相覆盖。子进程把每次要做的写入登记成
# 「效应」带回来，主进程在存储纪元校验通过后统一落地（apply_effects）。

_EFFECTS: list | None = None          # 非 None = 当前处于效应收集模式


def enable_effects() -> None:
    """子进程抓取入口调用：此后本进程内的所有缓存写入改为收集效应、不落盘。"""
    global _EFFECTS
    _EFFECTS = []


def take_effects() -> list:
    """取走本轮收集的效应并退出收集模式（一对一，异常也不会串进下一次）。"""
    global _EFFECTS
    out = _EFFECTS or []
    _EFFECTS = None
    return out


def record_effect(kind: str, payload: dict) -> bool:
    """收集模式下登记一条效应并短路真正的写入；非收集模式返回 False（照常写）。"""
    if _EFFECTS is None:
        return False
    _EFFECTS.append((kind, payload))
    return True


def apply_effects(effects, epoch: int | None = None) -> int:
    """主进程落地子进程带回的效应；返回实际应用条数。

    与 `set_storage_dir` 互斥（storage.SWITCH_LOCK）：`epoch` 与当前纪元不一致
    （抓取期间切了库）→ 整批作废，旧项目的学习绝不写进新目录的缓存。
    **一批只调一次**（/api/fetch/status 曾双调导致策略 runs 双计的教训）。
    """
    if not effects:
        return 0
    # 延迟 import：反向调用兄弟模块的落地函数，顶层 import 会成环
    from almond.core import hints_cookie, hints_strategy
    with storage.SWITCH_LOCK:
        if epoch is not None and epoch != storage.storage_epoch():
            return 0
        n = 0
        for kind, p in effects:
            try:
                if kind == "touch":
                    _touch(p["url"], p["key"], p["value"])
                elif kind == "save_strategy":
                    hints_strategy.save_strategy(p["url"], p["strategy_id"], p.get("ms"))
                elif kind == "drop_strategy":
                    hints_strategy.drop_strategy(p["url"], p.get("strategy_id"))
                elif kind == "drop_cookie":
                    hints_cookie.drop_cookie(p["url"])
                elif kind == "touch_cookie":
                    hints_cookie.touch_cookie(p["url"], p.get("ms"), new_cookies=p.get("cookies") or {})
                elif kind == "store_cookie":
                    hints_cookie.store_cookie(p["url"], p.get("cookies") or {})
                else:
                    continue
                n += 1
            except Exception:  # noqa: BLE001 —— 单条效应坏了不拖垮整批
                continue
        return n


def fetch_stale() -> bool:
    """本次抓取期间切换了存储目录 → 调用方应中止剩余抓取工作。

    `write_allowed()` 只挡**写入**（旧经验不落进新目录），挡不住已经跑起来的
    三级引擎——不主动检查的话，切库后后台还会继续烧浏览器/超时跑完整条链。
    未绑定纪元（脚本直调）返回 False。
    """
    epoch = getattr(_FETCH_EPOCH, "epoch", None)
    return epoch is not None and epoch != storage.storage_epoch()


# ---------------------------------------------------------------- 缓存存取（兄弟模块的依赖面）

def path() -> Path:
    """缓存文件位置（跟随存储目录，切换后自动指向新位置）。"""
    return storage.data_dir() / "cache" / HINTS_FILENAME


def load() -> dict:
    """读缓存（内存态；进程内共享，调用方持 LOCK 修改后调 save()）。"""
    global _DATA
    if _DATA is None:
        parsed: dict = {}
        try:
            f = path()
            if f.exists():
                # utf-8-sig：Windows 记事本/PowerShell 写出的带 BOM 文件也能读
                raw = json.loads(f.read_text(encoding="utf-8-sig"))
                if isinstance(raw, dict):
                    parsed = raw
        except (OSError, json.JSONDecodeError):
            parsed = {}          # 文件损坏当没有经验，下次写入会覆盖
        parsed.setdefault("hosts", {})
        _DATA = parsed
    return _DATA


def save() -> None:
    """原子落盘（写 tmp 后 replace），读方不会看到半截文件。"""
    f = path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.parent / (f.name + ".tmp")
    tmp.write_text(json.dumps(_DATA, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def host_of(url: str) -> str:
    """经验行的键 = 根域名；裸域名原样返回。"""
    return hostutil.root_domain(url) or url


# ---------------------------------------------------------------- 可达性经验

def _touch(url: str, key: str, value: bool) -> None:
    """记一条经验；值没变化就不落盘（并发高频调用下避免写放大）。"""
    if not write_allowed():
        return
    if record_effect("touch", {"url": url, "key": key, "value": value}):
        return
    domain = host_of(url)
    with LOCK:
        row = load()["hosts"].setdefault(domain, {})
        if row.get(key) == value:
            return
        row[key] = value
        row["updated"] = datetime.now().strftime(TS_FMT)
        save()


def hint(url: str) -> dict:
    """读某 URL 的经验行：{direct: bool|None, proxy: bool|None, updated: str}。"""
    with LOCK:
        row = load()["hosts"].get(host_of(url), {})
        return {
            "direct": row.get("direct"),
            "proxy": row.get("proxy"),
            "updated": row.get("updated", ""),
        }


def all_hints() -> dict:
    """全部经验行，供 /api/domains 附带给前端展示（系统实测面板的数据源）。

    每行：`direct`/`proxy`/`updated`（可达性结论）+ **`strategy`**（当前活跃方案 id）+
    **`plan`**（有没有可用抓取方案 = 活跃策略 **或** 已存 Cookie——Cookie 直取也算方案；
    只暴露布尔，Cookie 值本身依旧不进 API）+ **`confirm`**（方案确认阶段的结论
    `{ok, at, reason}`，null = 未确认过）。前端 `autoFetchEligible` 按 `plan` 放行
    自动抓取：先确认、后抓取。
    """
    with LOCK:
        out = {}
        for domain, row in load()["hosts"].items():
            sid = (row.get("strategy") or {}).get("id")
            out[domain] = {
                "direct": row.get("direct"),
                "proxy": row.get("proxy"),
                "updated": row.get("updated", ""),
                "strategy": sid,
                "plan": bool(sid) or bool((row.get("cookie") or {}).get("value")),
                "confirm": row.get("confirm"),
            }
        return out


def record_direct_success(url: str) -> None:
    """直连收到过 HTTP 响应（任何状态码）= 直连可达。"""
    _touch(url, "direct", True)


def record_direct_failure(url: str) -> None:
    """直连网络不可达（超时/重置/DNS 失败）= 这个域名必须另想办法。"""
    _touch(url, "direct", False)


def record_proxy_success(url: str) -> None:
    """经代理收到过 HTTP 响应 = 代理能到达这个域名。"""
    _touch(url, "proxy", True)


def is_network_error(text: str) -> bool:
    """错误文本是否属于「网络不可达」类（可据此记直连失败）。"""
    low = (text or "").lower()
    return any(marker in low for marker in _NETWORK_MARKERS)


def proxy_needed(url: str) -> bool:
    """已知直连不通 → 抓取应只走代理、不再跑直连兜底。"""
    return hint(url)["direct"] is False


# ---------------------------------------------------------------- 跳过判定

def skip_reason(url: str, proxy: str | None) -> str | None:
    """返回「这条现在不该抓」的原因；可以抓则返回 None。

    - 已知直连不通 + 没有代理        → 跳过，等有代理再试
    - 已知直连不通 + 代理转发不通    → 跳过，留日志等代理恢复再试
    - 已知直连不通 + 代理可用        → 返回 None，调用方应**只走代理**（proxy_needed）
    """
    h = hint(url)
    if h["direct"] is not False:
        return None
    domain = host_of(url)
    if not proxy:
        return (f"已知 {domain} 直连不通（经验缓存记录于 {h['updated'] or '未知时间'}），"
                f"当前没有代理 → 本次不抓取，待有代理后重试")
    if not proxy_reachable(proxy):
        return (f"已知 {domain} 必须走代理，但代理 {proxy} 转发不通"
                f"（代理不通导致抓取不成功）→ 本次不抓取，待代理恢复后重试")
    return None


# ---------------------------------------------------------------- 方案确认（confirm 阶段）

def domain_plan(url: str) -> bool:
    """该域名当前**有没有可用抓取方案**：活跃策略 或 已存 Cookie（Cookie 直取也算方案）。

    入参既可以是完整 URL 也可以是裸域名（`host_of()` 对裸名原样返回）。
    这是自动抓取的放行判据——`plan=True` 才允许自动抓这一组（先确认、后抓取）。
    """
    with LOCK:
        row = load()["hosts"].get(host_of(url), {})
        return bool((row.get("strategy") or {}).get("id")) or bool(
            (row.get("cookie") or {}).get("value")
        )


def confirm_domain(url: str, ok: bool, reason: str = "") -> None:
    """写入该域名的方案确认结论（confirm 阶段专用，主进程直接落盘）。

    结论**持久且一次性**：`ok=False`（多路尝试均不通）→ 自动抓取整组跳过、
    之后不再重测；`ok=True` 后若方案又失效（策略被弃用、Cookie 被清），
    `domain_plan()` 变 False 而 confirm 仍在 → 同样跳过、不再重试（用户语义：
    确认过但当前没有方案，就不要再尝试了）。
    """
    if not write_allowed():
        return
    domain = host_of(url)
    now = datetime.now().strftime(TS_FMT)
    with LOCK:
        row = load()["hosts"].setdefault(domain, {})
        row["confirm"] = {"ok": bool(ok), "at": now, "reason": reason or ""}
        row["updated"] = now
        save()


def get_confirm(url: str) -> dict | None:
    """该域名的确认结论 {ok, at, reason}；从未确认过返回 None。"""
    with LOCK:
        c = load()["hosts"].get(host_of(url), {}).get("confirm")
        return dict(c) if isinstance(c, dict) else None


def _on_storage_changed() -> None:
    """切库监听（由 storage.set_storage_dir 触发）：丢弃内存中的经验缓存——
    下一次 load() 落到**新目录**（新目录没有本文件就从空开始，绝不带旧项目的学习）。"""
    global _DATA
    _DATA = None


storage.on_storage_changed(_on_storage_changed)
