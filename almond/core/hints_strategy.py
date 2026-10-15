"""策略成绩册：记住每个域名「哪套打法抓到过数据」，下次最快者优先。

一个*策略* = 任何一次抓到过数据的配方：`路由|引擎[|参数…]`，
如 `direct|static`、`proxy|static|impersonate=firefox147`（路由切换也算——
代理→直连回退成功会记下 `direct|static`）。
每个域名一个成绩册 `strategies: {sid: {runs, best_ms, last_ms, last_success}}`
加一个活跃指针 `strategy: {id, last_success, best_ms}` = **已知最快的那套**。

存储原语全部来自 fetch_hints（效应通道与纪元守卫在那里，本模块只写数据）：
`LOCK` / `load()` / `save()` / `write_allowed()` / `record_effect()` / `host_of()` / `TS_FMT`。
"""

from datetime import datetime, timedelta

from almond.core.fetch_hints import (
    LOCK, TS_FMT, host_of, load, record_effect, save, write_allowed,
)

STRATEGY_STALE_DAYS = 7      # 超过 N 天没成功用过 → 下次使用时强制复验
_STRATEGY_BOARD_MAX = 6      # 每域名成绩册上限（超出丢最慢的备胎）


def get_strategy(url: str) -> dict | None:
    """当前应使用的策略 = **成绩册里最快的一套**：{id, last_success, best_ms}。"""
    with LOCK:
        s = load()["hosts"].get(host_of(url), {}).get("strategy")
        if isinstance(s, dict) and s.get("id"):
            return {
                "id": s["id"],
                "last_success": s.get("last_success", ""),
                "best_ms": s.get("best_ms"),
            }
    return None


def strategy_stale(url: str) -> bool:
    """距上次成功使用是否超过 STRATEGY_STALE_DAYS（时间缺失/解析不了按过期处理）。"""
    s = get_strategy(url)
    if not s:
        return False
    try:
        last = datetime.strptime(s["last_success"], TS_FMT)
    except (ValueError, TypeError):
        return True
    return datetime.now() - last > timedelta(days=STRATEGY_STALE_DAYS)


def save_strategy(url: str, strategy_id: str, ms: int | None = None) -> None:
    """记一次成功使用（ms = 本次抓取耗时）：更新成绩册，**更快者当选**。

    strategy_id 编码完整配方：`路由|引擎[|参数…]`，例如
      `direct|static`（直连静态抓取成功）
      `proxy|static|impersonate=firefox147`（走代理 + firefox 指纹）
    成绩册 `strategies`：sid → {runs, best_ms, last_ms, last_success}。
    当选规则：同 id 刷新；当前无策略 / 当前无历史成绩（升级前的老数据）/ 本次更快 → 当选；
    比当前慢 → 只进成绩册当备胎。每次成功都刷新 last_success（7 天复验凭证）。
    """
    if not write_allowed():
        return
    if record_effect("save_strategy", {"url": url, "strategy_id": strategy_id, "ms": ms}):
        return
    domain = host_of(url)
    with LOCK:
        row = load()["hosts"].setdefault(domain, {})
        now = datetime.now().strftime(TS_FMT)
        board = row.setdefault("strategies", {})
        rec = dict(board.get(strategy_id) or {})
        rec["runs"] = int(rec.get("runs") or 0) + 1
        rec["last_success"] = now
        if ms is not None:
            rec["last_ms"] = ms
            if rec.get("best_ms") is None or ms < rec["best_ms"]:
                rec["best_ms"] = ms
        board[strategy_id] = rec

        active = row.get("strategy")
        faster = (
            not active                                   # 第一套 → 当选
            or active.get("id") == strategy_id           # 同一套 → 刷新
            or active.get("best_ms") is None             # 老数据无成绩 → 用这次实测的
            or (ms is not None and ms < active.get("best_ms"))   # 真更快 → 夺位
        )
        if faster:
            row["strategy"] = {
                "id": strategy_id,
                "last_success": now,
                "best_ms": rec.get("best_ms"),
            }
        _trim_board(row)
        row["updated"] = now
        save(domain)


def _trim_board(row: dict) -> None:
    """成绩册只留最快 N 套（活跃的必留，其余按 best_ms 升序）。"""
    board = row.get("strategies") or {}
    if len(board) <= _STRATEGY_BOARD_MAX:
        return
    active_id = (row.get("strategy") or {}).get("id")
    ranked = sorted(
        board,
        key=lambda k: (board[k].get("best_ms") is None, board[k].get("best_ms") or 0),
    )
    ordered = ([active_id] if active_id in board else []) + [k for k in ranked if k != active_id]
    for k in list(board):
        if k not in ordered[:_STRATEGY_BOARD_MAX]:
            board.pop(k)


def drop_strategy(url: str, strategy_id: str | None = None) -> None:
    """弃用一套策略（缺省 = 当前最快的那套）：从成绩册除名。

    被弃用的正是活跃位时，**顺位给成绩册里第二快的**（曾经成功过、还没被证伪）；
    成绩册清空则整个移除，等下一轮被拦时重新定制。
    """
    if not write_allowed():
        return
    if record_effect("drop_strategy", {"url": url, "strategy_id": strategy_id}):
        return
    domain = host_of(url)
    with LOCK:
        row = load()["hosts"].get(domain)
        if not row:
            return
        active = row.get("strategy") or {}
        sid = strategy_id or active.get("id")
        if not sid:
            return
        board = row.get("strategies") or {}
        board.pop(sid, None)
        if active.get("id") == sid:
            if board:
                nid = min(
                    board,
                    key=lambda k: (board[k].get("best_ms") is None,
                                   board[k].get("best_ms") or 0),
                )
                row["strategy"] = {
                    "id": nid,
                    "last_success": board[nid].get("last_success", ""),
                    "best_ms": board[nid].get("best_ms"),
                }
            else:
                row.pop("strategy", None)
        row["updated"] = datetime.now().strftime(TS_FMT)
        save(domain)
