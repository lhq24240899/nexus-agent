"""
任务统计追踪器 —— 记录每次任务的执行情况, 计算成功率和趋势
对应 HyperAgents 的评分机制 + Auto-Evolve 的量化追踪
"""
import sqlite3
import time
from config import DATA_DIR

DB_PATH = DATA_DIR / "nexus.db"


class TaskStatsTracker:
    """任务统计追踪"""

    def __init__(self, db_conn: sqlite3.Connection = None):
        if db_conn:
            self.conn = db_conn
        else:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._init_table()

    def _init_table(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS task_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task TEXT,
                success INTEGER,
                tool_count INTEGER,
                duration REAL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                skill_used TEXT,
                fast_path INTEGER,
                timestamp TEXT
            )
        """)
        self.conn.commit()

    def record(self, task: str, success: bool, tool_count: int,
               duration: float, input_tokens: int = 0, output_tokens: int = 0,
               skill_used: str = "", fast_path: bool = False):
        """记录一次任务执行"""
        self.conn.execute(
            """INSERT INTO task_stats
               (task, success, tool_count, duration, input_tokens, output_tokens, skill_used, fast_path, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task[:200], 1 if success else 0, tool_count, duration,
             input_tokens, output_tokens, skill_used,
             1 if fast_path else 0,
             time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        self.conn.commit()

    def summary(self, limit: int = 20) -> dict:
        """统计摘要"""
        total = self.conn.execute("SELECT COUNT(*) FROM task_stats").fetchone()[0]
        if total == 0:
            return {"total": 0, "success_rate": 0, "avg_tools": 0,
                    "avg_duration": 0, "avg_tokens": 0, "recent": []}

        success = self.conn.execute("SELECT COUNT(*) FROM task_stats WHERE success=1").fetchone()[0]
        avg_tools = self.conn.execute("SELECT AVG(tool_count) FROM task_stats").fetchone()[0] or 0
        avg_duration = self.conn.execute("SELECT AVG(duration) FROM task_stats").fetchone()[0] or 0
        avg_input = self.conn.execute("SELECT AVG(input_tokens) FROM task_stats").fetchone()[0] or 0
        avg_output = self.conn.execute("SELECT AVG(output_tokens) FROM task_stats").fetchone()[0] or 0

        # 最近 N 次的趋势
        recent = self.conn.execute(
            """SELECT task, success, tool_count, duration, timestamp
               FROM task_stats ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()

        # 最近10次成功率 (短期趋势)
        recent_10 = self.conn.execute(
            "SELECT success FROM task_stats ORDER BY id DESC LIMIT 10"
        ).fetchall()
        recent_success = sum(1 for r in recent_10 if r[0] == 1)
        recent_rate = recent_success / len(recent_10) * 100 if recent_10 else 0

        return {
            "total": total,
            "success_rate": round(success / total * 100, 1),
            "recent_success_rate": round(recent_rate, 1),
            "avg_tools": round(avg_tools, 1),
            "avg_duration": round(avg_duration, 1),
            "avg_tokens": round(avg_input + avg_output),
            "recent": [
                {"task": r[0][:50], "success": bool(r[1]),
                 "tools": r[2], "duration": round(r[3], 1), "time": r[4]}
                for r in recent
            ],
        }
