"""
日志模块 —— 对应视频中"秘书和Nexus的配合可以在日志中看到"
"""
import json
import time
from pathlib import Path
from config import DATA_DIR

LOG_FILE = DATA_DIR / "agent_log.jsonl"


class AgentLogger:
    """结构化日志, 每条记录一个 JSON, 方便 UI 展示"""

    def __init__(self):
        self.logs: list[dict] = []
        self._load()

    def _load(self):
        if LOG_FILE.exists():
            for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        self.logs.append(json.loads(line))
                    except Exception:
                        pass

    def log(self, role: str, action: str, detail: str = "", **extra):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "timestamp": time.time(),
            "role": role,           # secretary / nexus / system / linux
            "action": action,       # 动作描述
            "detail": detail,       # 详细内容
            **extra,
        }
        self.logs.append(entry)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # 控制台也打印
        print(f"[{entry['time']}] [{role:>9}] {action}  {detail[:60]}")

    def recent(self, n: int = 50) -> list[dict]:
        return self.logs[-n:]

    def clear(self):
        self.logs = []
        LOG_FILE.write_text("", encoding="utf-8")


logger = AgentLogger()
