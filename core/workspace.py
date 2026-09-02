"""
工作空间管理器
==============
每个工作空间 = 一个项目根目录 + 独立的数据目录(对话历史/四库/代码索引)
切换工作空间时, 所有数据自动隔离, 互不干扰
"""
import json
import os
import hashlib
from pathlib import Path
from config import DATA_DIR

WORKSPACES_FILE = DATA_DIR / "workspaces.json"


class WorkspaceManager:
    """工作空间管理: 创建/列出/切换/删除"""

    def __init__(self):
        self._ensure_file()
        self._load()
        self._migrate_legacy_data()

    def _migrate_legacy_data(self):
        """首次启动时, 将旧版 data/ 目录下的数据迁移到 default 工作空间"""
        import shutil
        default_dir = self.data_dir("default")
        # 1. 对话历史迁移
        old_hist = DATA_DIR / "conversation_history.json"
        new_hist = default_dir / "conversation_history.json"
        if old_hist.exists() and not new_hist.exists():
            shutil.copy2(str(old_hist), str(new_hist))
            print(f"[workspace] 已迁移旧对话历史 -> {new_hist}")
        # 2. 四库数据库迁移 (仅当新库为空库时覆盖, 避免覆盖已有数据)
        old_db = DATA_DIR / "nexus.db"
        new_db = default_dir / "nexus.db"
        if old_db.exists():
            if not new_db.exists() or new_db.stat().st_size < 8192:
                # 先删除旧的 WAL/SHM 文件, 避免新主文件与旧 WAL 不匹配导致数据读不到
                for suffix in ("", "-wal", "-shm"):
                    p = default_dir / f"nexus.db{suffix}"
                    if p.exists():
                        p.unlink()
                shutil.copy2(str(old_db), str(new_db))
                print(f"[workspace] 已迁移旧四库数据库 -> {new_db}")

    def _ensure_file(self):
        if not WORKSPACES_FILE.exists():
            default = {
                "current": "default",
                "workspaces": [
                    {"name": "default", "path": str(Path.cwd()), "created_at": ""}
                ]
            }
            WORKSPACES_FILE.write_text(
                json.dumps(default, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

    def _load(self):
        data = json.loads(WORKSPACES_FILE.read_text(encoding="utf-8"))
        self.current_name = data.get("current", "default")
        self.workspaces = data.get("workspaces", [])

    def _save(self):
        WORKSPACES_FILE.write_text(
            json.dumps({"current": self.current_name, "workspaces": self.workspaces},
                       ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def list(self) -> list[dict]:
        return self.workspaces

    def get_current(self) -> dict:
        for ws in self.workspaces:
            if ws["name"] == self.current_name:
                return ws
        return self.workspaces[0] if self.workspaces else {}

    def data_dir(self, name: str = None) -> Path:
        """获取工作空间的数据目录 (按工作空间名分子目录, 避免同项目多工作空间共享数据)"""
        name = name or self.current_name
        ws = next((w for w in self.workspaces if w["name"] == name), None)
        if ws:
            # 数据放在项目根目录的 .nexus/<工作空间名>/ 下, 项目自包含且互相隔离
            d = Path(ws["path"]) / ".nexus" / name
        else:
            d = DATA_DIR / ("ws_" + name)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def history_file(self, name: str = None) -> Path:
        return self.data_dir(name) / "conversation_history.json"

    def library_db(self, name: str = None) -> Path:
        return self.data_dir(name) / "nexus.db"

    def code_index_db(self, name: str = None) -> Path:
        return self.data_dir(name) / "code_index.db"

    def create(self, name: str, path: str) -> dict:
        """创建新工作空间"""
        path = str(Path(path).resolve())
        if any(w["name"] == name for w in self.workspaces):
            return {"error": "工作空间名已存在"}
        if not Path(path).exists():
            return {"error": "目录不存在: " + path}
        ws = {"name": name, "path": path, "created_at": ""}
        self.workspaces.append(ws)
        self.data_dir(name)  # 确保目录创建
        self._save()
        return ws

    def switch(self, name: str) -> dict:
        """切换工作空间, 返回新工作空间信息"""
        if not any(w["name"] == name for w in self.workspaces):
            return {"error": "工作空间不存在"}
        self.current_name = name
        self._save()
        return self.get_current()

    def delete(self, name: str) -> dict:
        """删除工作空间(不删除项目文件, 只删除.nexus数据)"""
        if name == "default":
            return {"error": "不能删除默认工作空间"}
        ws = next((w for w in self.workspaces if w["name"] == name), None)
        if not ws:
            return {"error": "工作空间不存在"}
        # 删除数据目录
        data_dir = self.data_dir(name)
        if data_dir.exists():
            import shutil
            shutil.rmtree(data_dir, ignore_errors=True)
        self.workspaces = [w for w in self.workspaces if w["name"] != name]
        if self.current_name == name:
            self.current_name = "default"
        self._save()
        return {"deleted": name}
