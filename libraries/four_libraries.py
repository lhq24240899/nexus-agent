"""
四库管理 —— 对应视频中秘书管理的: 工具库 / 知识库 / 经验库 / 记忆库
SQLite 持久化 + 内存向量检索 ("标点定位")
自动从旧版 JSON 文件迁移
"""
import json
import sqlite3
import time
from pathlib import Path
from config import DATA_DIR
from libraries.vector_store import VectorStore

DB_PATH = DATA_DIR / "nexus.db"


class Library:
    """单个库: SQLite 持久化 + 向量检索"""

    def __init__(self, name: str, vector_store: VectorStore, db_conn: sqlite3.Connection):
        self.name = name
        self.vector_store = vector_store
        self.conn = db_conn
        self._init_table()
        self._migrate_from_json()
        # 加载到向量库
        for item in self.all():
            vid = self._vid(item["id"])
            if vid not in self.vector_store.documents:
                self.vector_store.add(vid, item["content"])

    def _vid(self, item_id: int) -> str:
        return f"{self.name}_{item_id}"

    def _init_table(self):
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                meta TEXT DEFAULT '{{}}',
                timestamp TEXT
            )
        """)
        self.conn.commit()

    def _migrate_from_json(self):
        """首次运行时从旧版 JSON 迁移"""
        json_path = DATA_DIR / f"{self.name}.json"
        if not json_path.exists():
            return
        # 检查 SQLite 是否已有数据
        count = self.conn.execute(f"SELECT COUNT(*) FROM {self.name}").fetchone()[0]
        if count > 0:
            return  # 已有数据, 不重复迁移
        try:
            items = json.loads(json_path.read_text(encoding="utf-8"))
            for item in items:
                self.conn.execute(
                    f"INSERT INTO {self.name} (id, content, meta, timestamp) VALUES (?, ?, ?, ?)",
                    (item["id"], item["content"],
                     json.dumps(item.get("meta", {}), ensure_ascii=False),
                     item.get("timestamp", "")),
                )
            self.conn.commit()
            print(f"[SQLite] {self.name}: 从 JSON 迁移 {len(items)} 条")
        except Exception as e:
            print(f"[SQLite] {self.name} 迁移失败: {e}")

    def add(self, content: str, meta: dict | None = None) -> dict:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        cursor = self.conn.execute(
            f"INSERT INTO {self.name} (content, meta, timestamp) VALUES (?, ?, ?)",
            (content, json.dumps(meta or {}, ensure_ascii=False), timestamp),
        )
        self.conn.commit()
        item_id = cursor.lastrowid
        self.vector_store.add(self._vid(item_id), content)
        return {"id": item_id, "content": content, "meta": meta or {}, "timestamp": timestamp}

    def clear(self):
        """清空所有条目 (同时清理向量)"""
        # 先清理向量
        for vid in list(self.vector_store.documents.keys()):
            if vid.startswith(f"{self.name}_"):
                self.vector_store.delete(vid)
        self.conn.execute(f"DELETE FROM {self.name}")
        self.conn.commit()

    def delete(self, item_id: int) -> bool:
        cursor = self.conn.execute(f"DELETE FROM {self.name} WHERE id = ?", (item_id,))
        self.conn.commit()
        if cursor.rowcount > 0:
            self.vector_store.delete(self._vid(item_id))
            return True
        return False

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        results = self.vector_store.search(query, top_k=top_k * 2)
        found = []
        for vid, score in results:
            if not vid.startswith(f"{self.name}_"):
                continue
            try:
                item_id = int(vid.split("_", 1)[1])
                row = self.conn.execute(
                    f"SELECT id, content, meta, timestamp FROM {self.name} WHERE id = ?",
                    (item_id,),
                ).fetchone()
                if row and score > 0:
                    found.append({
                        "id": row[0], "content": row[1],
                        "meta": json.loads(row[2]) if row[2] else {},
                        "timestamp": row[3], "score": round(score, 4),
                    })
            except (ValueError, IndexError):
                pass
        return found[:top_k]

    def all(self) -> list[dict]:
        rows = self.conn.execute(
            f"SELECT id, content, meta, timestamp FROM {self.name} ORDER BY id"
        ).fetchall()
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
        return self.conn.execute(f"SELECT COUNT(*) FROM {self.name}").fetchone()[0]


class FourLibraries:
    """四库总控: 工具库 / 知识库 / 经验库 / 记忆库"""

    def __init__(self):
        self.vector_store = VectorStore()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self.tools = Library("tools", self.vector_store, self.conn)
        self.knowledge = Library("knowledge", self.vector_store, self.conn)
        self.experience = Library("experience", self.vector_store, self.conn)
        self.memory = Library("memory", self.vector_store, self.conn)

    def search_all(self, query: str, top_k: int = 3) -> dict[str, list[dict]]:
        return {
            "工具库": self.tools.search(query, top_k),
            "知识库": self.knowledge.search(query, top_k),
            "经验库": self.experience.search(query, top_k),
            "记忆库": self.memory.search(query, top_k),
        }

    def stats(self) -> dict:
        return {
            "检索模式": self.vector_store.mode,
            "向量库文档数": len(self.vector_store),
            "工具库": len(self.tools),
            "知识库": len(self.knowledge),
            "经验库": len(self.experience),
            "记忆库": len(self.memory),
            "存储": "SQLite",
        }
