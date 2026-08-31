"""
临时工作目录管理
================
测试 / 验证 / 演示用的一次性文件统一落到项目根的 ``temp/`` 下:

    temp/task_<时间>_<pid>_<线程id>/   <- 每个任务一个独立目录

生命周期:
- DualCoreAgent.run / run_stream 开始时调用 :func:`begin_task`
- 任务目录路径通过 :func:`context_hint` 注入给决策核心的上下文
- 任务结束(含异常/客户端断开)时 :func:`end_task` 整个删除
- 进程异常退出的残留, 由 :func:`cleanup_stale` 在下次启动时清理
- 进程正常退出时 atexit 兜底清理本进程创建过的目录

线程安全: Flask 每个请求一个线程, 用 threading.local 绑定"当前任务目录",
并发请求互不干扰。

安全边界: 所有删除操作都限制在 TEMP_ROOT 内, 路径穿越(如 ``..\\..\\``)直接拒绝。
"""
import atexit
import os
import shutil
import threading
import time
from pathlib import Path

from config import BASE_DIR

TEMP_ROOT: Path = BASE_DIR / "temp"
_STALE_AFTER_HOURS = 12.0

_lock = threading.Lock()
_tls = threading.local()
# 本进程创建过的全部任务目录(跨线程), atexit 兜底用
_process_dirs: set[Path] = set()


def _ensure_root() -> Path:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    return TEMP_ROOT


def begin_task() -> Path:
    """为当前线程的任务创建独立的临时目录, 返回其绝对路径"""
    root = _ensure_root()
    base_name = f"task_{time.strftime('%H%M%S')}_{os.getpid()}_{threading.get_ident() % 100000}"
    task_dir = root / base_name
    suffix = 1
    while task_dir.exists():  # 极小概率重名时追加序号
        task_dir = root / f"{base_name}_{suffix}"
        suffix += 1
    task_dir.mkdir(parents=True)
    _tls.task_dir = task_dir
    with _lock:
        _process_dirs.add(task_dir.resolve())
    return task_dir


def get_task_dir(create: bool = False) -> Path | None:
    """获取当前线程绑定的任务目录; create=True 时不存在则懒创建"""
    task_dir = getattr(_tls, "task_dir", None)
    if task_dir is None and create:
        return begin_task()
    return task_dir


def context_hint() -> str:
    """注入给决策核心的临时目录规则文本(含本次任务的绝对路径)"""
    task_dir = get_task_dir(create=True)
    return (
        "【临时工作目录规则 —— 必须遵守】\n"
        f"1. 本次任务所有测试/验证/演示用的一次性文件, 必须写入: {task_dir}\n"
        "2. 严禁把临时文件写到项目根目录或其他位置\n"
        "3. 验证完成后必须主动调用 cleanup_temp 工具清理本次产生的临时文件\n"
        "4. 正式源码和用户要求持久保留的文件禁止放入(任务结束该目录会被自动清空)"
    )


def _safe_resolve(base: Path, relative_path: str) -> Path:
    """把相对路径拼到 base 下并防路径穿越"""
    target = (base / relative_path).resolve()
    base_resolved = base.resolve()
    if target != base_resolved and base_resolved not in target.parents:
        raise ValueError("路径越界: 只能操作临时工作目录内的文件")
    return target


def _remove_one(path: Path) -> tuple[int, int, int]:
    """删除单个文件/目录, 返回 (文件数, 目录数, 字节数)"""
    if not path.exists() and not path.is_symlink():
        return 0, 0, 0
    if path.is_dir() and not path.is_symlink():
        files = dirs = size = 0
        for child in path.rglob("*"):
            try:
                if child.is_file() or child.is_symlink():
                    size += child.stat().st_size
                    files += 1
                elif child.is_dir():
                    dirs += 1
            except OSError:
                pass
        shutil.rmtree(path, ignore_errors=False)
        dirs += 1  # 自身
        return files, dirs, size
    size = path.stat().st_size
    path.unlink()
    return 1, 0, size


def cleanup_temp(relative_path: str = "") -> dict:
    """
    清理当前任务的临时文件。
    - relative_path 为空: 清空整个任务目录(保留目录本身)
    - 非空: 只删除目录内指定的子文件/子目录
    """
    task_dir = get_task_dir(create=False)
    if task_dir is None or not task_dir.exists():
        return {"ok": True, "removed_files": 0, "removed_dirs": 0,
                "freed_bytes": 0, "note": "当前任务没有临时目录, 无需清理"}

    relative_path = (relative_path or "").strip().strip("/\\")
    files = dirs = freed = 0

    if relative_path:
        target = _safe_resolve(task_dir, relative_path)
        if not target.exists():
            return {"ok": False, "removed_files": 0, "removed_dirs": 0,
                    "freed_bytes": 0, "note": f"临时目录中不存在: {relative_path}"}
        files, dirs, freed = _remove_one(target)
    else:
        for child in list(task_dir.iterdir()):
            try:
                f, d, b = _remove_one(child)
                files += f
                dirs += d
                freed += b
            except OSError:
                pass

    return {"ok": True, "removed_files": files, "removed_dirs": dirs,
            "freed_bytes": freed, "task_dir": str(task_dir)}


def end_task() -> dict:
    """任务结束: 整个任务目录删除并解绑当前线程"""
    task_dir = getattr(_tls, "task_dir", None)
    stats = {"removed_files": 0, "removed_dirs": 0, "freed_bytes": 0}
    if task_dir is not None:
        try:
            if task_dir.exists():
                files, dirs, freed = _remove_one(task_dir)
                stats = {"removed_files": files, "removed_dirs": dirs,
                         "freed_bytes": freed}
        except OSError:
            pass
        with _lock:
            _process_dirs.discard(task_dir.resolve())
        _tls.task_dir = None
    return stats


def cleanup_stale(max_age_hours: float = _STALE_AFTER_HOURS) -> int:
    """启动时清理异常退出残留的旧任务目录, 返回清理个数"""
    if not TEMP_ROOT.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for child in TEMP_ROOT.glob("task_*"):
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    return removed


@atexit.register
def _atexit_cleanup() -> None:
    """进程退出时兜底清理本进程创建的任务目录"""
    with _lock:
        dirs = list(_process_dirs)
    for path in dirs:
        try:
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass
