"""
四库管理 —— 对应视频中秘书管理的: 工具库 / 知识库 / 经验库 / 记忆库
SQLite 持久化 + 内存向量检索 ("标点定位")
自动从旧版 JSON 文件迁移
"""
import json
import time
from config import DATA_DIR
from utils.db import get_db
from libraries.vector_store import VectorStore

DB_PATH = DATA_DIR / "nexus.db"


class Library:
    """单个库: SQLite 持久化 + 向量检索"""

    def __init__(self, name: str, vector_store: VectorStore, db=None):
        self.name = name
        self.vector_store = vector_store
        self.db = db or get_db()
        self.conn = self.db.conn
        self._init_table()
        self._migrate_from_json()
        # 批量加载到向量库 (防抖落盘, 整批只写一次)
        self.vector_store.begin_bulk()
        try:
            for item in self.all():
                vid = self._vid(item["id"])
                if vid not in self.vector_store.documents:
                    self.vector_store.add(vid, item["content"])
        finally:
            self.vector_store.end_bulk()

    def _vid(self, item_id: int) -> str:
        return f"{self.name}_{item_id}"

    def _init_table(self):
        with self.db.transaction():
            self.conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    meta TEXT DEFAULT '{{}}',
                    timestamp TEXT
                )
            """)

    def _migrate_from_json(self):
        """首次运行时从旧版 JSON 迁移"""
        json_path = DATA_DIR / f"{self.name}.json"
        if not json_path.exists():
            return
        # 检查 SQLite 是否已有数据
        count = self.db.query_one(f"SELECT COUNT(*) FROM {self.name}")[0]
        if count > 0:
            return  # 已有数据, 不重复迁移
        try:
            items = json.loads(json_path.read_text(encoding="utf-8"))
            with self.db.transaction():
                for item in items:
                    self.conn.execute(
                        f"INSERT INTO {self.name} (id, content, meta, timestamp) VALUES (?, ?, ?, ?)",
                        (item["id"], item["content"],
                         json.dumps(item.get("meta", {}), ensure_ascii=False),
                         item.get("timestamp", "")),
                    )
            print(f"[SQLite] {self.name}: 从 JSON 迁移 {len(items)} 条")
        except Exception as e:
            print(f"[SQLite] {self.name} 迁移失败: {e}")

    def add(self, content: str, meta: dict | None = None, mode: str | None = None) -> dict:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        meta = meta or {}
        if mode:
            meta["mode"] = mode
        cursor = self.db.execute(
            f"INSERT INTO {self.name} (content, meta, timestamp) VALUES (?, ?, ?)",
            (content, json.dumps(meta, ensure_ascii=False), timestamp),
        )
        item_id = cursor.lastrowid
        self.vector_store.add(self._vid(item_id), content)
        return {"id": item_id, "content": content, "meta": meta, "timestamp": timestamp}

    def clear(self):
        """清空所有条目 (同时清理向量)"""
        # 先清理向量
        for vid in list(self.vector_store.documents.keys()):
            if vid.startswith(f"{self.name}_"):
                self.vector_store.delete(vid)
        self.db.execute(f"DELETE FROM {self.name}")

    def delete(self, item_id: int) -> bool:
        cursor = self.db.execute(
            f"DELETE FROM {self.name} WHERE id = ?", (item_id,))
        if cursor.rowcount > 0:
            self.vector_store.delete(self._vid(item_id))
            return True
        return False

    def delete_before(self, max_id: int) -> int:
        """删除 id <= max_id 的所有条目并同步清理向量, 返回删除条数 (用于异步压缩)"""
        rows = self.db.query(
            f"SELECT id FROM {self.name} WHERE id <= ?", (max_id,))
        ids = [r[0] for r in rows]
        if not ids:
            return 0
        self.db.execute(f"DELETE FROM {self.name} WHERE id <= ?", (max_id,))
        for iid in ids:
            self.vector_store.delete(self._vid(iid))
        return len(ids)

    def search(self, query: str, top_k: int = 3,
               meta_filter: dict = None, weight: float = 1.0) -> list[dict]:
        results = self.vector_store.search(query, top_k=top_k * 3)
        found = []
        for vid, score in results:
            if not vid.startswith(f"{self.name}_"):
                continue
            try:
                item_id = int(vid.split("_", 1)[1])
                row = self.db.query_one(
                    f"SELECT id, content, meta, timestamp FROM {self.name} WHERE id = ?",
                    (item_id,),
                )
                if row and score > 0:
                    meta = json.loads(row[2]) if row[2] else {}
                    # meta 过滤: 只保留匹配指定 meta 字段的条目
                    if meta_filter:
                        if not all(meta.get(k) == v for k, v in meta_filter.items()):
                            continue
                    found.append({
                        "id": row[0], "content": row[1],
                        "meta": meta,
                        "timestamp": row[3],
                        "score": round(score * weight, 4),
                        "raw_score": round(score, 4),
                    })
            except (ValueError, IndexError):
                pass
        # 按加权分数排序
        found.sort(key=lambda x: x["score"], reverse=True)
        return found[:top_k]

    def all(self) -> list[dict]:
        rows = self.db.query(
            f"SELECT id, content, meta, timestamp FROM {self.name} ORDER BY id"
        )
        return [
            {"id": r[0], "content": r[1],
             "meta": json.loads(r[2]) if r[2] else {}, "timestamp": r[3]}
            for r in rows
        ]

    def summary(self) -> str:
        items = self.all()
        if not items:
            return "(空)"
        return "\n".join(f"[{i['id']}] {i['content'][:80]}" for i in items)

    def __len__(self):
        return self.db.query_one(f"SELECT COUNT(*) FROM {self.name}")[0]


class FourLibraries:
    """四库总控: 工具库 / 知识库 / 经验库 / 记忆库"""

    def __init__(self):
        self.vector_store = VectorStore()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        db = get_db()
        self.conn = db.conn
        self.tools = Library("tools", self.vector_store, db)
        self.knowledge = Library("knowledge", self.vector_store, db)
        self.experience = Library("experience", self.vector_store, db)
        self.memory = Library("memory", self.vector_store, db)

    # 三模式差异化检索配置
    SEARCH_CONFIG = {
        "work": {
            "top_k": {"工具库": 5, "知识库": 4, "经验库": 3, "记忆库": 2},
            "weights": {"工具库": 1.0, "知识库": 1.0, "经验库": 1.3, "记忆库": 0.8},
        },
        "chat": {
            "top_k": {"工具库": 3, "知识库": 3, "经验库": 3, "记忆库": 3},
            "weights": {"工具库": 0.5, "知识库": 0.9, "经验库": 0.8, "记忆库": 1.3},
        },
        "brainstorm": {
            "top_k": {"工具库": 2, "知识库": 5, "经验库": 4, "记忆库": 2},
            "weights": {"工具库": 0.3, "知识库": 1.2, "经验库": 1.1, "记忆库": 0.5},
        },
    }
    # 记忆库只检索用户偏好, 排除任务流水账
    MEMORY_FILTER = {"type": "user_preference"}

    def search_all(self, query: str, top_k: int = 3,
                   mode: str = "work") -> dict[str, list[dict]]:
        cfg = self.SEARCH_CONFIG.get(mode, self.SEARCH_CONFIG["work"])
        tks = cfg["top_k"]
        wts = cfg["weights"]
        return {
            "工具库": self.tools.search(query, tks["工具库"], weight=wts["工具库"]),
            "知识库": self.knowledge.search(query, tks["知识库"], weight=wts["知识库"]),
            "经验库": self.experience.search(query, tks["经验库"], weight=wts["经验库"]),
            "记忆库": self.memory.search(query, tks["记忆库"],
                                         meta_filter=self.MEMORY_FILTER,
                                         weight=wts["记忆库"]),
        }

    def stats(self) -> dict:
        return {
            "检索模式": self.vector_store.mode,
            "向量库文档数": len(self.vector_store),
            "工具库": len(self.tools),
            "知识库": len(self.knowledge),
            "经验库": len(self.experience),
            "记忆库": len(self.memory),
            "存储": "SQLite-WAL",
        }
