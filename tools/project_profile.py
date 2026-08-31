"""
项目档案管理 —— 沉淀项目技术栈/启动/测试命令/约定, 重复任务直接注入
对应"高效编码": 不用每次重新 project_analyze, 档案自动召回
"""
import json
import os
import time
from utils.db import get_db


class ProjectProfileManager:
    """项目档案: 按项目根目录存储技术画像, 编码任务前自动注入"""

    def __init__(self, db=None):
        self.db = db or get_db()
        self.conn = self.db.conn
        self._init_table()

    def _init_table(self):
        with self.db.transaction():
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS project_profiles (
                    path TEXT PRIMARY KEY,
                    languages TEXT DEFAULT '[]',
                    frameworks TEXT DEFAULT '[]',
                    test_frameworks TEXT DEFAULT '[]',
                    entry_files TEXT DEFAULT '[]',
                    dep_files TEXT DEFAULT '[]',
                    conventions TEXT DEFAULT '',
                    last_analyzed TEXT
                )
            """)

    @staticmethod
    def _dumps(obj) -> str:
        return json.dumps(obj, ensure_ascii=False)

    @staticmethod
    def _loads(text: str, default):
        try:
            return json.loads(text) if text else default
        except Exception:
            return default

    def save(self, path: str, languages: list = None, frameworks: list = None,
             test_frameworks: list = None, entry_files: list = None,
             dep_files: list = None, conventions: str = "") -> dict:
        """保存/更新项目档案 (path 为项目根目录绝对路径)"""
        abs_path = os.path.abspath(path)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self.db.execute(
            """INSERT INTO project_profiles
               (path, languages, frameworks, test_frameworks, entry_files, dep_files, conventions, last_analyzed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                 languages=excluded.languages, frameworks=excluded.frameworks,
                 test_frameworks=excluded.test_frameworks,
                 entry_files=excluded.entry_files, dep_files=excluded.dep_files,
                 conventions=excluded.conventions, last_analyzed=excluded.last_analyzed""",
            (abs_path, self._dumps(languages or []), self._dumps(frameworks or []),
             self._dumps(test_frameworks or []), self._dumps(entry_files or []),
             self._dumps(dep_files or []), conventions, now),
        )
        return {"ok": True, "path": abs_path, "saved_at": now}

    def get(self, path: str) -> dict | None:
        row = self.db.query_one(
            "SELECT path, languages, frameworks, test_frameworks, entry_files, dep_files, conventions, last_analyzed FROM project_profiles WHERE path = ?",
            (os.path.abspath(path),),
        )
        if not row:
            return None
        return {
            "path": row[0],
            "languages": self._loads(row[1], []),
            "frameworks": self._loads(row[2], []),
            "test_frameworks": self._loads(row[3], []),
            "entry_files": self._loads(row[4], []),
            "dep_files": self._loads(row[5], []),
            "conventions": row[6] or "",
            "last_analyzed": row[7] or "",
        }

    def get_for_directory(self, dir_path: str) -> dict | None:
        """查找目录所属项目的档案 (最长前缀匹配), 用于编码任务前自动召回"""
        abs_dir = os.path.abspath(dir_path)
        # 取所有档案路径, 找 abs_dir 以其为前缀的最长匹配
        rows = self.db.query("SELECT path FROM project_profiles")
        candidates = [r[0] for r in rows
                      if abs_dir == r[0] or abs_dir.startswith(r[0].rstrip("/\\") + os.sep)]
        if not candidates:
            return None
        best = max(candidates, key=len)
        return self.get(best)

    def list(self) -> list[dict]:
        rows = self.db.query(
            "SELECT path, languages, frameworks, test_frameworks, entry_files, dep_files, conventions, last_analyzed FROM project_profiles ORDER BY last_analyzed DESC"
        )
        return [
            {
                "path": r[0],
                "languages": self._loads(r[1], []),
                "frameworks": self._loads(r[2], []),
                "test_frameworks": self._loads(r[3], []),
                "entry_files": self._loads(r[4], []),
                "dep_files": self._loads(r[5], []),
                "conventions": r[6] or "",
                "last_analyzed": r[7] or "",
            }
            for r in rows
        ]

    def delete(self, path: str) -> bool:
        cursor = self.db.execute(
            "DELETE FROM project_profiles WHERE path = ?", (os.path.abspath(path),))
        return cursor.rowcount > 0

    def format_for_context(self, profile: dict) -> str:
        """把档案格式化为可注入决策核心的上下文文本"""
        if not profile:
            return ""
        lines = [f"【项目档案】{profile['path']}"]
        if profile["languages"]:
            lines.append(f"语言: {', '.join(profile['languages'])}")
        if profile["frameworks"]:
            lines.append(f"框架: {', '.join(profile['frameworks'])}")
        if profile["test_frameworks"]:
            lines.append(f"测试: {', '.join(profile['test_frameworks'])}")
        if profile["entry_files"]:
            lines.append(f"入口: {', '.join(profile['entry_files'][:5])}")
        if profile["dep_files"]:
            lines.append(f"依赖: {', '.join(profile['dep_files'][:5])}")
        if profile["conventions"]:
            lines.append(f"约定: {profile['conventions']}")
        if profile["last_analyzed"]:
            lines.append(f"档案更新于: {profile['last_analyzed']}")
        return "\n".join(lines)
