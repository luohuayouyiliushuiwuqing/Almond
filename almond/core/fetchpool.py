"""抓取子进程池：`/api/fetch` 的抓取工作跑在独立进程里，不跑在 Web 进程里。

为什么不能用线程（用户两条硬要求）：
1. **切换存储后要立即硬停**——线程无法安全中断正在跑的引擎调用（只能等它
   自己超时返回，最坏一分钟）；子进程 `terminate` 即刻生效，Windows 上通过
   Job Object（KILL_ON_JOB_CLOSE）把它拉起的 chromium 等**整棵进程树**一起杀掉，
   POSIX 上用独立进程组 + killpg，不留孤儿浏览器。
2. **抓取不得阻塞任何其它东西**——Web 进程不再跑 scrapling（GIL 争用归零），
   代理面板、切库接口永远秒回。

子进程**不直接写 fetch_hints 缓存**（跨进程没有那把 RLock，各写各的会互相覆盖）：
子进程内 `enable_effects()` 把每次要做的写入登记成「效应」带回来，
主进程在存储纪元校验通过后由 `fetch_hints.apply_effects()` 落地。

生命周期：懒启动（首次提交拉起 WORKERS 个子进程）；`cancel_all()`（切库监听
触发）硬杀全部并让在等的任务立刻以 None 收口；下次提交再懒重启。
"""
import itertools
import multiprocessing as mp
import os
import queue
import threading

from almond.core import fetch_hints, records
from almond.core.fetcher import get_metadata
from almond.core.log import logger

_CTX = mp.get_context("spawn")     # 统一 spawn：Windows 必须，POSIX 上避 fork+线程
_WORKERS = 8                        # 与前端补抓 5 并发一一对应
_WAIT_TIMEOUT = 400.0              # 单任务看门狗：正常超时链远短于此，防子进程挂死

# 「一代」= 一对 mp.Queue + 一个读线程 + 本代子进程。
# 为什么按代整体换：子进程被 terminate 时若正往结果队列里写，管道可能留下
# 半截数据（multiprocessing 的已知坑）——读线程 get() 一抛异常就再也收不到任何
# 结果，之后的抓取全部吊死。cancel_all 时整代作废（旧读线程随 sentinel 退出），
# 下次提交用全新队列，绝不复用被污染的管道。
_gen: dict | None = None           # {"in_q", "out_q", "retired"}
_procs: list = []
_jobs: dict = {}                   # job_id → threading.Queue（结果或 None=已被硬停）
_job_epochs: dict = {}             # job_id → 提交时的存储纪元（异步模式：提交与结果处理分属不同请求）
_lock = threading.Lock()
_next_id = itertools.count(1)


# ---------------------------------------------------------------- 进程树随进程消亡

def _make_process_tree_killable() -> None:
    """把自己放进「随进程消亡整树陪葬」的容器。

    Windows：创建 Job Object（KILL_ON_JOB_CLOSE）并把自己纳入——父进程
    terminate 导致本进程退出、句柄被内核关闭时，作业里所有进程（含 chromium
    后代）一并被杀。POSIX：setsid 独立进程组，父进程 killpg 整组。
    任何一步失败都只降级为「只杀本进程」，不阻断抓取。
    """
    if os.name != "nt":
        try:
            os.setsid()
        except OSError:
            pass
        return
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = k32.CreateJobObjectW(None, None)
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        )
        ok = ok and k32.AssignProcessToJobObject(job, k32.GetCurrentProcess())
        if not ok:
            k32.CloseHandle(job)
            return
        global _JOB_HANDLE
        _JOB_HANDLE = job      # 句柄必须被引用着——GC 关掉它会当场杀掉自己
    except Exception:  # noqa: BLE001 —— 降级为只杀本进程，不阻断抓取
        pass


_JOB_HANDLE = None     # Windows Job Object 句柄（仅子进程持有）


# ---------------------------------------------------------------- 子进程侧

def _child_main(in_q, out_q) -> None:
    """子进程主循环：取任务 → get_metadata → 把 meta 与效应发回。"""
    _make_process_tree_killable()
    while True:
        try:
            task = in_q.get()
        except Exception:  # noqa: BLE001 —— 管道被父进程作废：安静退出
            break
        if task is None:            # 温和退出信号（当前只用硬杀，保留此口）
            break
        job_id, kwargs = task
        meta = None
        effects = []
        try:
            fetch_hints.enable_effects()   # 每个任务独立一轮（take 即关）
            meta = get_metadata(**kwargs)
        except Exception as exc:  # noqa: BLE001 —— 兜底：坏任务不拖垮循环
            logger.warning(f"抓取子进程任务异常（{type(exc).__name__}: {exc}）：{kwargs.get('url')}")
        finally:
            effects = fetch_hints.take_effects()
        try:
            out_q.put((job_id, meta, effects))
        except Exception:  # noqa: BLE001 —— 代已作废（切库）时静默
            break


# ---------------------------------------------------------------- 父进程侧

def _reader(gen: dict) -> None:
    """本代唯一的 mp 结果取数线程：按 job_id 分发到各自的等待队列。

    只认自己这一代：代被 cancel_all 作废后，父进程往旧 out_q 塞一个 sentinel
    把这里唤醒退出——绝不消费新代的数据，也不因旧管道的残骸而僵死。
    """
    out_q = gen["out_q"]
    while not gen["retired"]:
        try:
            item = out_q.get()
        except Exception:  # noqa: BLE001 —— 旧管道残骸：本代到此为止
            return
        if item is None:            # 代结束哨兵
            return
        job_id, meta, effects = item
        with _lock:
            jq = _jobs.pop(job_id, None)
        if jq is not None:
            jq.put((meta, effects))


def _new_gen() -> None:
    """开新一代（队列 + 读线程）；调用时必须持有 _lock。"""
    global _gen
    in_q = _CTX.Queue()
    out_q = _CTX.Queue()
    gen = {"in_q": in_q, "out_q": out_q, "retired": False}
    _gen = gen
    threading.Thread(target=_reader, args=(gen,), daemon=True, name="fetchpool-reader").start()


def _ensure_pool() -> None:
    """懒建代/子进程（都在 _lock 内调用）。"""
    if _gen is None:
        _new_gen()
    _procs[:] = [p for p in _procs if p.is_alive()]
    started = 0
    while len(_procs) < _WORKERS:
        p = _CTX.Process(
            target=_child_main, args=(_gen["in_q"], _gen["out_q"]),
            daemon=True, name="fetch-child",
        )
        p.start()
        _procs.append(p)
        started += 1
    if started:
        logger.info(f"抓取子进程池：拉起 {started} 个子进程（本代共 {len(_procs)}）")


def submit(epoch: int = 0, **kwargs) -> int:
    """提交一个 get_metadata 任务，返回 job_id（配合 wait/peek 收结果）。

    epoch：提交时的存储纪元——异步模式下提交与结果处理分属不同请求线程，
    后者需要知道提交时的纪元来做 stale 判定（存储切换后旧结果作废）。
    """
    with _lock:
        _ensure_pool()
        job_id = next(_next_id)
        _jobs[job_id] = queue.Queue()
        _job_epochs[job_id] = epoch
        in_q = _gen["in_q"]
    in_q.put((job_id, kwargs))
    return job_id


def wait(job_id: int, timeout: float = _WAIT_TIMEOUT):
    """等任务结果。

    返回 `(meta, effects)`；**None = 任务已被硬停**（切库 cancel_all 收口）；
    超时抛 `TimeoutError`（看门狗——子进程挂死时别把请求吊到天荒地老）。
    """
    with _lock:
        jq = _jobs.get(job_id)
    if jq is None:
        return None
    try:
        item = jq.get(timeout=timeout)
    except queue.Empty:
        with _lock:
            _jobs.pop(job_id, None)
        raise TimeoutError(f"抓取子进程 {int(timeout)}s 无响应") from None
    with _lock:
        _jobs.pop(job_id, None)
        _job_epochs.pop(job_id, None)
    return item


def peek(job_id: int) -> dict:
    """非阻塞查询任务结果（供异步 /api/fetch/status 轮询）。

    返回 {"done": True, "result": (meta, effects)}  或  {"done": False}。
    任务被硬停（切库）时 result=None，done=True。
    """
    with _lock:
        jq = _jobs.get(job_id)
        epoch = _job_epochs.get(job_id, 0)
    if jq is None:
        # 任务已被 cancel_all 清掉（切库），或从未存在
        return {"done": True, "result": None, "epoch": epoch}
    try:
        item = jq.get_nowait()
    except queue.Empty:
        return {"done": False, "epoch": epoch}
    # 任务完成：拿到结果，清理元数据
    with _lock:
        _jobs.pop(job_id, None)
        _job_epochs.pop(job_id, None)
    return {"done": True, "result": item, "epoch": epoch}


def get_job_epoch(job_id: int) -> int:
    """查询任务的提交纪元（供 /api/fetch/status 做 stale 判定）。"""
    with _lock:
        return _job_epochs.get(job_id, 0)


def cancel_all() -> None:
    """硬停全部抓取子进程（切库监听调用）：在等的任务立刻收到 None。

    `terminate` 打断**正在执行的引擎调用**（含浏览器），不是等它自己超时——
    这就是「切库后抓取立即停掉」的实现。子进程死后 Job/进程组连带其后代一起收掉。
    队列与读线程整代作废（管道可能被写坏），下次提交开新代。
    """
    global _gen
    with _lock:
        jobs = list(_jobs.values())
        _jobs.clear()
        _job_epochs.clear()          # 纪元映射一并清掉，不留陈年 stale 数据
        procs = list(_procs)
        _procs.clear()
        gen, _gen = _gen, None
    for jq in jobs:
        jq.put(None)
    killed = 0
    for p in procs:
        try:
            if os.name == "nt":
                p.terminate()
            else:
                try:
                    import signal
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    p.kill()
            killed += 1
        except Exception:  # noqa: BLE001 —— 已退出的进程不计较
            continue
    for p in procs:
        try:
            p.join(timeout=1.0)
        except Exception:  # noqa: BLE001
            pass
    if gen is not None:
        gen["retired"] = True
        try:
            gen["out_q"].put(None)          # 唤醒并结束旧读线程
        except Exception:  # noqa: BLE001
            pass
    if killed:
        logger.info(f"存储切换：已硬停 {killed} 个抓取子进程（含其浏览器进程树）")


records.on_storage_changed(cancel_all)
