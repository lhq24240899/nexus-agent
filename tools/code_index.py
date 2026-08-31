"""
代码符号索引 —— 启动时扫描项目, 提取函数/类/变量定义
支持 Python / JavaScript / TypeScript
code_search 优先查索引, 找不到再回退 grep
"""
import re
import sqlite3
import os
from pathlib import Path
from config import DATA_DIR

DB_PATH = DATA_DIR / "nexus.db"

# 支持的语言和对应的符号提取正则
LANG_PATTERNS = {
    ".py": [
        (r"^class\s+(\w+)", "class"),
        (r"^def\s+(\w+)", "function"),
        (r"^async\s+def\s+(\w+)", "function"),
    ],
    ".js": [
        (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
        (r"^(?:export\s+)?class\s+(\w+)", "class"),
        (r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(", "function"),
    ],
    ".ts": [
        (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
        (r"^(?:export\s+)?class\s+(\w+)", "class"),
        (r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*[:=]", "variable"),
        (r"^(?:export\s+)?interface\s+(\w+)", "interface"),
    ],
    ".tsx": [
        (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
        (r"^(?:export\s+)?class\s+(\w+)", "class"),
        (r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*[:=]", "variable"),
    ],
    ".jsx": [
        (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
        (r"^(?:export\s+)?class\s+(\w+)", "class"),
    ],
}

# 跳过的目录
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "dist", "build", ".next", ".nuxt", "data", "logs"}


class CodeIndex:
    """代码符号索引"""

    def __init__(self, db_conn: sqlite3.Connection = None):
        if db_conn:
            self.conn = db_conn
        else:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._init_table()

    def _init_table(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS code_symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_path TEXT,
                file_path TEXT,
                symbol_name TEXT,
                symbol_type TEXT,
                line_number INTEGER,
                snippet TEXT
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol_name ON code_symbols(symbol_name)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol_file ON code_symbols(file_path)")
        self.conn.commit()

    def index_project(self, project_path: str) -> dict:
        """扫描项目目录, 建立符号索引"""
        project_path = os.path.abspath(project_path)
        # 清除该项目的旧索引
        self.conn.execute("DELETE FROM code_symbols WHERE project_path = ?", (project_path,))

        count = 0
        for root, dirs, files in os.walk(project_path):
            # 跳过不需要的目录
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in LANG_PATTERNS:
                    continue

                fpath = os.path.join(root, fname)
                rel_path = os.path.relpath(fpath, project_path)
                patterns = LANG_PATTERNS[ext]

                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        for lineno, line in enumerate(f, 1):
                            for pattern, stype in patterns:
                                m = re.match(pattern, line.strip())
                                if m:
                                    name = m.group(1)
                                    snippet = line.strip()[:120]
                                    self.conn.execute(
                                        "INSERT INTO code_symbols (project_path, file_path, symbol_name, symbol_type, line_number, snippet) VALUES (?, ?, ?, ?, ?, ?)",
                                        (project_path, rel_path, name, stype, lineno, snippet),
                                    )
                                    count += 1
                                    break
                except (IOError, OSError):
                    continue

        self.conn.commit()
        return {"project": project_path, "symbols": count}

    def search(self, query: str, project_path: str = None, limit: int = 20) -> list[dict]:
        """按符号名搜索, 支持模糊匹配"""
        if project_path:
            rows = self.conn.execute(
                """SELECT file_path, symbol_name, symbol_type, line_number, snippet
                   FROM code_symbols
                   WHERE project_path = ? AND symbol_name LIKE ?
                   ORDER BY symbol_type, symbol_name LIMIT ?""",
                (os.path.abspath(project_path), f"%{query}%", limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT file_path, symbol_name, symbol_type, line_number, snippet
                   FROM code_symbols
                   WHERE symbol_name LIKE ?
                   ORDER BY symbol_type, symbol_name LIMIT ?""",
                (f"%{query}%", limit),
            ).fetchall()

        return [
            {"file": r[0], "name": r[1], "type": r[2],
             "line": r[3], "snippet": r[4]}
            for r in rows
        ]

    def list_symbols(self, file_path: str = None, project_path: str = None, limit: int = 50) -> list[dict]:
        """列出所有符号, 可按文件过滤"""
        if file_path:
            rows = self.conn.execute(
                """SELECT file_path, symbol_name, symbol_type, line_number, snippet
                   FROM code_symbols WHERE file_path LIKE ? ORDER BY line_number LIMIT ?""",
                (f"%{file_path}%", limit),
            ).fetchall()
        elif project_path:
            rows = self.conn.execute(
                """SELECT file_path, symbol_name, symbol_type, line_number, snippet
                   FROM code_symbols WHERE project_path = ? ORDER BY file_path, line_number LIMIT ?""",
                (os.path.abspath(project_path), limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT file_path, symbol_name, symbol_type, line_number, snippet
                   FROM code_symbols ORDER BY file_path, line_number LIMIT ?""",
                (limit,),
            ).fetchall()

        return [
            {"file": r[0], "name": r[1], "type": r[2],
             "line": r[3], "snippet": r[4]}
            for r in rows
        ]

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM code_symbols").fetchone()[0]
        files = self.conn.execute("SELECT COUNT(DISTINCT file_path) FROM code_symbols").fetchone()[0]
        by_type = self.conn.execute(
            "SELECT symbol_type, COUNT(*) FROM code_symbols GROUP BY symbol_type"
        ).fetchall()
        return {"total_symbols": total, "files_indexed": files,
                "by_type": {r[0]: r[1] for r in by_type}}
