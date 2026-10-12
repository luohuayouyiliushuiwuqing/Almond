"""存储目录管理：本地私有数据统一存放处 + 存储纪元 + 切库互斥。

metadata.db、图片缓存、fetch_hints.json 都在「存储目录」下；目录可在界面「存储」
面板切换（**不迁移**：旧数据留原地，新目录从零开始）。

切库协议的三个互斥方共用本模块的 `SWITCH_LOCK`：
  - `set_storage_dir` 的目录/纪元改写；
  - `records.upsert_record_if_epoch` 的「比对纪元 + 写入」；
  - `fetch_hints.apply_effects` 的效应落地。
缺一把锁就有「比对通过 → 切库 → 写进新库」的 TOCTOU 窗口。
"""

import json
import os
import sqlite3
import threading
from pathlib import Path

# 仓库根：parents[0]=core, [1]=almond, [2]=仓库根（ALMOND_DATA_DIR 可兜底覆盖）。
# config.json 固定放这里——它只记录「本地数据放在哪个存储目录」，必须跟着代码走。
# 数据库、图片缓存等本地私有数据统一放在「存储目录」：默认仓库根，UI 里可切换（不迁移、旧数据留原地）。
_BASE_DIR = Path(os.environ.get("ALMOND_DATA_DIR") or Path(__file__).resolve().parents[2])
CONFIG_PATH = _BASE_DIR / "config.json"


def _load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)  # 目录被删后重建，写配置不崩
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


_cfg = _load_config()
_DATA_DIR = Path(_cfg["storage_dir"]).expanduser() if _cfg.get("storage_dir") else _BASE_DIR
_DB_PATH = _DATA_DIR / "metadata.db"

# 存储纪元：每次真实切换目录 +1。切换前发起的抓取，完成后比对纪元不一致 → 结果作废
# （防止旧库的 URL 被写进新库、旧经验写进新缓存）。同一目录重复保存不递增。
_STORAGE_EPOCH = 0
# 切库互斥锁：set_storage_dir 的目录/纪元改写 与 upsert_record_if_epoch 的
# 「比对纪元 + 写入」必须原子——否则存在「比对通过 → 切库 → 写进新库」的窗口
SWITCH_LOCK = threading.RLock()
# 切库后的回调（fetch_hints 在 import 时注册自己的内存缓存重置；
# fetchpool 注册 cancel_all 硬停抓取子进程）
_STORAGE_LISTENERS: list = []


def data_dir() -> Path:
    """当前存储目录（私有可变全局的唯一读口）。"""
    return _DATA_DIR


def db_path() -> Path:
    """当前 metadata.db 路径。"""
    return _DB_PATH


def storage_epoch() -> int:
    return _STORAGE_EPOCH


def on_storage_changed(callback) -> None:
    """注册切库回调；切换成功后按注册顺序执行（异常单个吞掉，不阻断切换）。"""
    _STORAGE_LISTENERS.append(callback)


# ──────────────────────── 存储目录（本地私有数据统一存放处） ────────────────────────

# 历史上限：够回看几处常用位置即可，多了反而难找（新用过的顶到最前）
_STORAGE_HISTORY_MAX = 10


def _push_storage_history(cfg: dict, path: str) -> None:
    """把目录记到历史头部（去重、截断）。cfg 由调用方负责落盘。"""
    hist = cfg.get("storage_history") or []
    kept = [p for p in hist if isinstance(p, str) and p != path]
    cfg["storage_history"] = ([path] + kept)[:_STORAGE_HISTORY_MAX]


def _storage_history(current: str) -> list[str]:
    """历史列表：当前目录永远置顶（老配置没有历史时也能看到现在在哪）。"""
    hist = [p for p in (_load_config().get("storage_history") or []) if isinstance(p, str)]
    return [current] + [p for p in hist if p != current]


def get_storage_paths() -> dict:
    """当前存储目录及其中的数据库、图片缓存位置，外加用过的历史目录。

    `epoch` 随处返回：前端开抓前要把它带上（/api/fetch 的请求级纪元校验），
    不能只在切库响应里出现——页面加载时也得知道当前是第几纪元。
    """
    return {
        "path": str(_DATA_DIR),
        "db_path": str(_DB_PATH),
        "cache_dir": str(_DATA_DIR / "cache" / "img"),
        "exists": _DATA_DIR.exists(),
        "history": _storage_history(str(_DATA_DIR)),
        "epoch": _STORAGE_EPOCH,
    }


def remove_storage_history(path: str) -> list[str]:
    """从历史里移除一条（当前目录移不掉——置顶展示由 _storage_history 保证）。"""
    path = (path or "").strip()
    cfg = _load_config()
    hist = cfg.get("storage_history") or []
    cfg["storage_history"] = [p for p in hist if isinstance(p, str) and p != path]
    _save_config(cfg)
    return _storage_history(str(_DATA_DIR))


def get_cache_dir() -> Path:
    """图片缓存目录（跟随存储目录，切换后自动指向新位置）。"""
    return _DATA_DIR / "cache" / "img"


def set_storage_dir(new_dir: str) -> dict:
    """切换存储目录：**不迁移，新目录从零开始**（用户定的语义）。

    - 旧数据库与缓存（图片、经验缓存）**原样留在原目录**——想用回旧数据随时切回去；
    - 目标已有 `metadata.db` 则直接使用它（只校验合法，内容不动）；没有则由下次连接
      自动创建**空库**；目录不存在会创建；相对路径按仓库根解析，传入文件则拒绝；
    - 同路径重复保存是 no-op（不递增纪元）；
    - 切换成功：存储纪元 +1（**切换前发起的抓取结果从此作废**）、依次执行
      `on_storage_changed` 注册的回调（fetch_hints 借此丢弃内存里的旧经验、
      fetchpool 硬停在途抓取）、历史位置记录新旧两处；结果写 config.json，
      下次启动沿用，调用后立即生效。
    """
    global _DATA_DIR, _DB_PATH, _STORAGE_EPOCH
    raw = (new_dir or "").strip()
    if not raw:
        raise ValueError("目录不能为空")
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = _BASE_DIR / target
    if target.is_file():
        raise ValueError(f"请指定目录而不是文件：{target}")
    try:
        target = target.resolve()
    except OSError as exc:
        raise ValueError(f"路径无效：{exc}") from exc

    old_data_dir = _DATA_DIR
    try:
        same = target == old_data_dir.resolve()
    except OSError:
        same = target == old_data_dir
    # 同路径但目录已不存在：不视为 no-op，往下走 mkdir 重建（修复场景）
    if same and target.is_dir():
        cfg = _load_config()
        _push_storage_history(cfg, str(target))   # 升级后的老配置首条历史从这里补上
        _save_config(cfg)
        return {**get_storage_paths(), "db_used_existing": _DB_PATH.exists()}

    target.mkdir(parents=True, exist_ok=True)
    dst_db = target / "metadata.db"
    db_used_existing = dst_db.exists()
    if db_used_existing:
        # 目标已有数据库：直接使用——只校验是合法 SQLite，内容一概不动
        try:
            probe = sqlite3.connect(dst_db)
            probe.execute("SELECT count(*) FROM sqlite_master")
            probe.close()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"目标目录的 metadata.db 不是有效的 SQLite 数据库：{dst_db}") from exc

    # 目录/纪元的改写与 upsert_record_if_epoch 的「比对+写入」共用 SWITCH_LOCK：
    # 保证不会出现「写方比对纪元通过 → 这里切库 → 写方落进新库」的窗口
    with SWITCH_LOCK:
        _DATA_DIR = target
        _DB_PATH = dst_db
        _STORAGE_EPOCH += 1                  # 从此刻起，旧纪元的抓取结果作废
    # 监听器放在锁**外**跑：fetchpool 的硬杀子进程要 join 几个进程，
    # 挂在锁里会把并发的写入/效应落地全都堵住（纪元已 +1，语义上已无需持锁）
    for _cb in list(_STORAGE_LISTENERS):
        try:
            _cb()
        except Exception:  # noqa: BLE001 —— 回调失败不阻断切换
            pass
    cfg = _load_config()
    if target == _BASE_DIR:
        cfg.pop("storage_dir", None)  # 切回默认目录，配置无需冗余记录
    else:
        cfg["storage_dir"] = str(target)
    cfg.pop("db_path", None)  # 清掉旧版「数据库单独指定位置」的配置键
    # 旧位置也留痕：从默认目录第一次切走时，老位置才不会从历史里消失
    _push_storage_history(cfg, str(old_data_dir))
    _push_storage_history(cfg, str(target))
    _save_config(cfg)
    return {**get_storage_paths(), "db_used_existing": db_used_existing, "epoch": _STORAGE_EPOCH}
