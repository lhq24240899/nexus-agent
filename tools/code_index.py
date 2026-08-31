"""
代码符号索引 —— 启动时扫描项目, 提取函数/类/变量定义
支持 Python / JavaScript / TypeScript
code_search 优先查索引, 找不到再回退 grep
"""
import re
import os
from config import DATA_DIR
from utils.db import get_db

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
             "dist", "build", ".next", ".nuxt", "data", "logs", "temp"}


class CodeIndex:
    """代码符号索引 (共享全局 SQLite 连接)"""

    def __init__(self, db_conn=None):
        self.db = get_db()
        self.conn = db_conn if db_conn is not None else self.db.conn
        self._init_table()

    def _init_table(self):
        with self.db.transaction():
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

    def index_project(self, project_path: str) -> dict:
        """扫描项目目录, 建立符号索引 (整批一个事务, 避免逐条 commit)"""
        project_path = os.path.abspath(project_path)
        rows = []

        for root, dirs, files in os.walk(project_path):
            # 跳过不需要的目录
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in LANG_PATTERNS:
                    continue

                fpath = os.path.join(root, fname)
                abs_path = os.path.abspath(fpath)
                patterns = LANG_PATTERNS[ext]

                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        for lineno, line in enumerate(f, 1):
                            for pattern, stype in patterns:
                                m = re.match(pattern, line.strip())
                                if m:
                                    rows.append((project_path, abs_path, m.group(1),
                                                 stype, lineno, line.strip()[:120]))
                                    break
                except (IOError, OSError):
                    continue

        with self.db.transaction():
            self.conn.execute(
                "DELETE FROM code_symbols WHERE project_path = ?", (project_path,))
            self.conn.executemany(
                "INSERT INTO code_symbols (project_path, file_path, symbol_name, symbol_type, line_number, snippet) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return {"project": project_path, "symbols": len(rows)}

    def search(self, query: str, project_path: str = None, limit: int = 20) -> list[dict]:
        """按符号名搜索, 支持模糊匹配"""
        if project_path:
            prefix = os.path.abspath(project_path).rstrip("/\\") + os.sep
            rows = self.db.query(
                """SELECT file_path, symbol_name, symbol_type, line_number, snippet
                   FROM code_symbols
                   WHERE (file_path LIKE ? OR project_path = ?) AND symbol_name LIKE ?
                   ORDER BY symbol_type, symbol_name LIMIT ?""",
                (prefix + "%", os.path.abspath(project_path), f"%{query}%", limit),
            )
        else:
            rows = self.db.query(
                """SELECT file_path, symbol_name, symbol_type, line_number, snippet
                   FROM code_symbols
                   WHERE symbol_name LIKE ?
                   ORDER BY symbol_type, symbol_name LIMIT ?""",
                (f"%{query}%", limit),
            )

        return [
            {"file": r[0], "name": r[1], "type": r[2],
             "line": r[3], "snippet": r[4]}
            for r in rows
        ]

    def list_symbols(self, file_path: str = None, project_path: str = None, limit: int = 50) -> list[dict]:
        """列出所有符号, 可按文件过滤"""
        if file_path:
            rows = self.db.query(
                """SELECT file_path, symbol_name, symbol_type, line_number, snippet
                   FROM code_symbols WHERE file_path LIKE ? ORDER BY line_number LIMIT ?""",
                (f"%{file_path}%", limit),
            )
        elif project_path:
            prefix = os.path.abspath(project_path).rstrip("/\\") + os.sep
            rows = self.db.query(
                """SELECT file_path, symbol_name, symbol_type, line_number, snippet
                   FROM code_symbols WHERE file_path LIKE ? OR project_path = ?
                   ORDER BY file_path, line_number LIMIT ?""",
                (prefix + "%", os.path.abspath(project_path), limit),
            )
        else:
            rows = self.db.query(
                """SELECT file_path, symbol_name, symbol_type, line_number, snippet
                   FROM code_symbols ORDER BY file_path, line_number LIMIT ?""",
                (limit,),
            )

        return [
            {"file": r[0], "name": r[1], "type": r[2],
             "line": r[3], "snippet": r[4]}
            for r in rows
        ]

    def index_file(self, file_path: str) -> dict:
        """单文件增量索引: 删除该文件旧符号 → 重新提取插入 (file_write/code_edit 后调用)

        file_path 存绝对路径, 与 index_project 保持一致, 搜索时按目录前缀匹配。
        非支持语言的文件直接返回 (不做任何操作)。
        """
        abs_path = os.path.abspath(file_path)
        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in LANG_PATTERNS:
            return {"file": abs_path, "symbols": 0, "skipped": True}
        if not os.path.isfile(abs_path):
            return {"file": abs_path, "symbols": 0, "error": "文件不存在"}

        patterns = LANG_PATTERNS[ext]
        rows = []
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    for pattern, stype in patterns:
                        m = re.match(pattern, line.strip())
                        if m:
                            rows.append((os.path.dirname(abs_path), abs_path,
                                         m.group(1), stype, lineno, line.strip()[:120]))
                            break
        except (IOError, OSError) as e:
            return {"file": abs_path, "symbols": 0, "error": str(e)}

        with self.db.transaction():
            # 删除该文件的旧符号 (绝对路径精确匹配)
            self.conn.execute("DELETE FROM code_symbols WHERE file_path = ?", (abs_path,))
            self.conn.executemany(
                "INSERT INTO code_symbols (project_path, file_path, symbol_name, symbol_type, line_number, snippet) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return {"file": abs_path, "symbols": len(rows)}

    def stats(self) -> dict:
        total = self.db.query_one("SELECT COUNT(*) FROM code_symbols")[0]
        files = self.db.query_one("SELECT COUNT(DISTINCT file_path) FROM code_symbols")[0]
        by_type_rows = self.db.query(
            "SELECT symbol_type, COUNT(*) FROM code_symbols GROUP BY symbol_type"
        )
        return {"total_symbols": total, "files_indexed": files,
                "by_type": {r[0]: r[1] for r in by_type_rows}}
