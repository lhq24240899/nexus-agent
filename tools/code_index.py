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

# ========== 多语言并发代码索引 (tree-sitter + ThreadPool + 按项目分库) ==========
import ast as _ast
import sqlite3 as _sqlite3
from pathlib import Path as _Path
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from tree_sitter_languages import get_parser as _get_parser
    _TS_AVAILABLE = True
except ImportError:
    _TS_AVAILABLE = False

# 支持的语言: 扩展名 -> tree-sitter语言名
LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
}

# 各语言的函数/类定义节点类型
DEF_NODES = {
    "python": ["function_definition", "class_definition"],
    "javascript": ["function_declaration", "class_declaration", "method_definition", "arrow_function"],
    "typescript": ["function_declaration", "class_declaration", "method_definition"],
    "tsx": ["function_declaration", "class_declaration", "method_definition"],
    "go": ["function_declaration", "method_declaration", "type_declaration"],
    "java": ["method_declaration", "class_declaration", "interface_declaration"],
    "rust": ["function_item", "struct_item", "impl_item"],
}

# 各语言的调用节点类型
CALL_NODES = {
    "python": ["call"],
    "javascript": ["call_expression"],
    "typescript": ["call_expression"],
    "tsx": ["call_expression"],
    "go": ["call_expression"],
    "java": ["method_invocation", "object_creation_expression"],
    "rust": ["call_expression"],
}


class MultiLangCodeIndex:
    """多语言代码符号索引: tree-sitter解析 + 并发构建 + 按项目分库"""

    def __init__(self, project_root=".", db_path=None, max_workers=4):
        self.root = _Path(project_root).resolve()
        self.max_workers = max_workers
        if db_path is None:
            # 按项目路径hash分库, 避免覆盖
            import hashlib
            safe_name = hashlib.md5(str(self.root).encode()).hexdigest()[:8]
            db_path = str(DATA_DIR / ("code_index_%s.db" % safe_name))
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = _sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, type TEXT NOT NULL,
                file_path TEXT NOT NULL, line INTEGER, end_line INTEGER,
                language TEXT, signature TEXT, docstring TEXT,
                UNIQUE(file_path, name, line)
            );
            CREATE TABLE IF NOT EXISTS refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_name TEXT NOT NULL, file_path TEXT NOT NULL,
                line INTEGER, context TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sym_name ON symbols(name);
            CREATE INDEX IF NOT EXISTS idx_ref_name ON refs(symbol_name);
            CREATE INDEX IF NOT EXISTS idx_sym_file ON symbols(file_path);
        """)
        conn.commit()
        conn.close()

    def build(self, progress_cb=None):
        """并发构建索引"""
        conn = _sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM refs")
        conn.commit()
        conn.close()

        skip = {".venv", "venv", "__pycache__", ".git", "node_modules",
                "temp", "data", "dist", "build", ".next", ".nuxt"}
        files = []
        for ext in LANG_MAP:
            files.extend(self.root.rglob("*" + ext))
        files = [f for f in files if not any(p in skip for p in f.parts)]
        total = len(files)

        # 并发解析
        all_symbols = []
        all_refs = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self._parse_file, f): f for f in files}
            done = 0
            for future in as_completed(futures):
                done += 1
                try:
                    syms, refs = future.result()
                    all_symbols.extend(syms)
                    all_refs.extend(refs)
                except Exception:
                    pass
                if progress_cb and done % 20 == 0:
                    progress_cb(done, total)

        # 批量写入
        conn = _sqlite3.connect(self.db_path)
        conn.executemany(
            "INSERT OR IGNORE INTO symbols VALUES (NULL,?,?,?,?,?,?,?,?)",
            all_symbols
        )
        conn.executemany(
            "INSERT INTO refs VALUES (NULL,?,?,?,?)",
            all_refs
        )
        conn.commit()
        conn.close()
        return {"files": total, "symbols": len(all_symbols), "refs": len(all_refs)}

    def _parse_file(self, fpath):
        """解析单个文件, 返回 (symbols, refs)"""
        ext = fpath.suffix.lower()
        lang = LANG_MAP.get(ext)
        if not lang:
            return [], []
        rel = str(fpath.relative_to(self.root))
        try:
            src = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return [], []
        lines = src.splitlines()

        # Python 用内置 ast (更准)
        if lang == "python":
            return self._parse_python(src, rel, lines)

        # 其他语言用 tree-sitter
        if not _TS_AVAILABLE:
            return [], []
        try:
            parser = _get_parser(lang)
            tree = parser.parse(bytes(src, "utf-8"))
            return self._parse_tree_sitter(tree, rel, lines, lang)
        except Exception:
            return [], []

    def _parse_python(self, src, rel, lines):
        symbols = []
        refs = []
        try:
            tree = _ast.parse(src)
        except Exception:
            return [], []
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args]
                sig = "%s(%s)" % (node.name, ", ".join(args))
                symbols.append((node.name, "function", rel, node.lineno,
                                getattr(node, "end_lineno", node.lineno),
                                "python", sig, (_ast.get_docstring(node) or "")[:200]))
            elif isinstance(node, _ast.ClassDef):
                symbols.append((node.name, "class", rel, node.lineno,
                                getattr(node, "end_lineno", node.lineno),
                                "python", "class %s" % node.name,
                                (_ast.get_docstring(node) or "")[:200]))
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Call):
                name = None
                if isinstance(node.func, _ast.Name):
                    name = node.func.id
                elif isinstance(node.func, _ast.Attribute):
                    name = node.func.attr
                if name and not name.startswith("_"):
                    ctx = lines[node.lineno - 1].strip()[:120] if node.lineno <= len(lines) else ""
                    refs.append((name, rel, node.lineno, ctx))
        return symbols, refs

    def _parse_tree_sitter(self, tree, rel, lines, lang):
        symbols = []
        refs = []
        def_types = DEF_NODES.get(lang, [])
        call_types = CALL_NODES.get(lang, [])

        def walk(node):
            ntype = node.type
            # 提取定义
            if ntype in def_types:
                name = self._extract_name(node)
                if name:
                    sig = self._extract_signature(node, lang)
                    symbols.append((name, ntype.replace("_declaration", "").replace("_definition", ""),
                                    rel, node.start_point[0] + 1, node.end_point[0] + 1,
                                    lang, sig, ""))
            # 提取调用
            if ntype in call_types:
                name = self._extract_call_name(node)
                if name and not name.startswith("_"):
                    line_no = node.start_point[0] + 1
                    ctx = lines[line_no - 1].strip()[:120] if line_no <= len(lines) else ""
                    refs.append((name, rel, line_no, ctx))
            for child in node.children:
                walk(child)

        walk(tree.root_node)
        return symbols, refs

    @staticmethod
    def _extract_name(node):
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode("utf-8", errors="replace")
        # property_identifier (JS方法)
        for child in node.children:
            if child.type == "property_identifier":
                return child.text.decode("utf-8", errors="replace")
        return None

    @staticmethod
    def _extract_call_name(node):
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode("utf-8", errors="replace")
            if child.type == "member_expression":
                for sub in child.children:
                    if sub.type == "property_identifier":
                        return sub.text.decode("utf-8", errors="replace")
        return None

    @staticmethod
    def _extract_signature(node, lang):
        try:
            text = node.text.decode("utf-8", errors="replace")
            # 取第一行作为签名
            first_line = text.split("\n")[0].strip()
            return first_line[:120]
        except Exception:
            return ""

    # ---------- 查询接口 ----------

    def find_definition(self, name):
        conn = _sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT name,type,file_path,line,end_line,language,signature,docstring FROM symbols WHERE name=? ORDER BY type,file_path",
            (name,)
        ).fetchall()
        conn.close()
        return [
            {"name": r[0], "type": r[1], "file": r[2], "line": r[3],
             "end_line": r[4], "language": r[5], "signature": r[6], "doc": (r[7] or "")[:100]}
            for r in rows
        ]

    def find_references(self, name):
        conn = _sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT symbol_name,file_path,line,context FROM refs WHERE symbol_name=? ORDER BY file_path,line",
            (name,)
        ).fetchall()
        conn.close()
        return [
            {"name": r[0], "file": r[1], "line": r[2], "context": r[3]}
            for r in rows
        ]

    def get_file_outline(self, file_path):
        conn = _sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT name,type,line,end_line,language,signature FROM symbols WHERE file_path LIKE ? ORDER BY line",
            ("%" + file_path + "%",)
        ).fetchall()
        conn.close()
        return [
            {"name": r[0], "type": r[1], "line": r[2], "end_line": r[3],
             "language": r[4], "signature": r[5]}
            for r in rows
        ]

    def stats(self):
        conn = _sqlite3.connect(self.db_path)
        s = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        r = conn.execute("SELECT COUNT(*) FROM refs").fetchone()[0]
        f = conn.execute("SELECT COUNT(DISTINCT file_path) FROM symbols").fetchone()[0]
        langs = conn.execute("SELECT language, COUNT(*) FROM symbols GROUP BY language").fetchall()
        conn.close()
        return {"files": f, "symbols": s, "references": r,
                "by_language": {l[0]: l[1] for l in langs}}


# 兼容旧名
ASTCodeIndex = MultiLangCodeIndex
