"""
日志模块 —— 对应视频中"秘书和Nexus的配合可以在日志中看到"

内存策略: 日志文件只增不减, 启动时不再全量载入内存,
而是流式读取并只保留最后 MAX_IN_MEMORY 条 (deque 自动淘汰),
避免运行越久内存占用越大。

日志轮转: agent_log.jsonl 超过 1MB 自动切割, 保留最近 3 个
(agent_log.jsonl.1 / .2 / .3), 防止日志无限膨胀。
"""

import json
import os
import threading
import time
from collections import deque

from config import DATA_DIR

LOG_FILE = DATA_DIR / "agent_log.jsonl"
MAX_IN_MEMORY = 500
MAX_LOG_BYTES = 1 * 1024 * 1024  # 超过 1MB 触发轮转
MAX_LOG_BACKUPS = 3  # 保留最近 3 个历史日志


def _rotate_if_needed():
    """日志文件超过阈值时轮转: .log -> .log.1 -> .log.2 -> .log.3 (丢弃最旧)"""
    try:
        if not LOG_FILE.exists():
            return
        if LOG_FILE.stat().st_size <= MAX_LOG_BYTES:
            return
        # 先删除最旧备份
        oldest = f"{LOG_FILE}.{MAX_LOG_BACKUPS}"
        if os.path.exists(oldest):
            os.remove(oldest)
        # 依次右移: .3 <- .2 <- .1
        for i in range(MAX_LOG_BACKUPS - 1, 0, -1):
            src = f"{LOG_FILE}.{i}"
            dst = f"{LOG_FILE}.{i + 1}"
            if os.path.exists(src):
                os.replace(src, dst)
        # 当前日志 -> .1
        os.replace(LOG_FILE, f"{LOG_FILE}.1")
    except OSError:
        pass


class AgentLogger:
    """结构化日志, 每条记录一个 JSON, 方便 UI 展示"""

    def __init__(self):
        self.logs: deque[dict] = deque(maxlen=MAX_IN_MEMORY)
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        """流式读取, 内存只保留最后 MAX_IN_MEMORY 条"""
        if not LOG_FILE.exists():
            return
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    self.logs.append(json.loads(line))
                except Exception:
                    pass

    def log(self, role: str, action: str, detail: str = "", **extra):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "timestamp": time.time(),
            "role": role,  # secretary / nexus / system / linux
            "action": action,  # 动作描述
            "detail": detail,  # 详细内容
            **extra,
        }
        with self._lock:
            self.logs.append(entry)
            # 写入前先检查是否需要轮转 (锁内保证多线程安全)
            _rotate_if_needed()
            # 锁内追加写文件, 保证多线程日志不交错
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # 控制台也打印
        print(f"[{entry['time']}] [{role:>9}] {action}  {detail[:60]}")

    def recent(self, n: int = 50) -> list[dict]:
        with self._lock:
            return list(self.logs)[-n:]

    def clear(self):
        with self._lock:
            self.logs.clear()
            LOG_FILE.write_text("", encoding="utf-8")


logger = AgentLogger()
