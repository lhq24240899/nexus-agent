"""
IMA 腾讯笔记 API 客户端
用于将 Nexus 经验库同步到 IMA Notes（云端备份）
"""
import os
import json
import logging
import threading
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_URL = "https://ima.qq.com"


class IMAClient:
    """IMA Notes API 客户端"""

    def __init__(self, client_id=None, api_key=None, folder_name=None):
        self.client_id = client_id or os.getenv("IMA_CLIENT_ID", "")
        self.api_key = api_key or os.getenv("IMA_API_KEY", "")
        self.folder_name = folder_name or os.getenv("IMA_SYNC_FOLDER", "Nexus经验库")
        self._folder_id = None
        self._session = requests.Session()

    @property
    def enabled(self):
        return bool(self.client_id and self.api_key and os.getenv("IMA_SYNC_ENABLED", "true").lower() == "true")

    def _headers(self):
        return {
            "ima-openapi-clientid": self.client_id,
            "ima-openapi-apikey": self.api_key,
            "Content-Type": "application/json",
        }

    def _post(self, path, body):
        """统一 POST 请求"""
        url = f"{BASE_URL}{path}"
        try:
            resp = self._session.post(url, headers=self._headers(), json=body, timeout=15)
            data = resp.json()
            if data.get("code") != 0:
                logger.warning(f"IMA API 错误: {data.get('code')} {data.get('msg')}")
                return None
            return data.get("data", {})
        except Exception as e:
            logger.warning(f"IMA API 请求失败: {e}")
            return None

    # ---------- 笔记本 ----------

    def list_notebooks(self):
        """列出所有笔记本"""
        data = self._post("/openapi/note/v1/list_notebook", {"cursor": "0", "limit": 20})
        if not data:
            return []
        return data.get("note_folder_infos", [])

    def get_or_create_folder(self):
        """获取或创建 Nexus 经验库笔记本，返回 folder_id"""
        if self._folder_id:
            return self._folder_id

        # 先找已有的
        notebooks = self.list_notebooks()
        for nb in notebooks:
            if nb.get("name") == self.folder_name:
                self._folder_id = nb.get("folder_id")
                logger.info(f"IMA: 找到已有笔记本 '{self.folder_name}' (id={self._folder_id})")
                return self._folder_id

        # 没有则创建（import_doc 时传 folder_name 会自动创建）
        logger.info(f"IMA: 笔记本 '{self.folder_name}' 不存在，将在首次导入时创建")
        return None

    # ---------- 笔记 ----------

    def import_doc(self, content, title=None):
        """
        从 Markdown 创建新笔记
        返回 note_id，失败返回 None
        """
        if not self.enabled:
            return None

        body = {
            "content_format": 1,  # MARKDOWN
            "content": content,
        }
        # 优先用 folder_id 精确匹配，避免和同名笔记本混淆
        folder_id = self.get_or_create_folder()
        if folder_id:
            body["folder_id"] = folder_id
        else:
            body["folder_name"] = self.folder_name
        if title:
            # title 放在 content 开头作为 H1
            body["content"] = f"# {title}\n\n{content}"

        data = self._post("/openapi/note/v1/import_doc", body)
        if data:
            note_id = data.get("note_id")
            logger.info(f"IMA: 笔记创建成功 note_id={note_id}")
            return note_id
        return None

    def append_doc(self, note_id, content):
        """追加 Markdown 内容到已有笔记"""
        if not self.enabled or not note_id:
            return False

        body = {
            "note_id": note_id,
            "content_format": 1,  # MARKDOWN
            "content": content,
        }
        data = self._post("/openapi/note/v1/append_doc", body)
        return data is not None

    def search_note(self, query, search_content=True):
        """搜索笔记，返回结果列表"""
        body = {
            "search_type": 1 if search_content else 0,  # 1=正文搜索, 0=标题搜索
            "sort_type": 0,  # 按修改时间
            "query_info": {"content": query, "title": query},
            "start": 0,
            "end": 20,
        }
        data = self._post("/openapi/note/v1/search_note", body)
        if not data:
            return []
        return data.get("search_note_infos", [])

    def get_note_content(self, note_id):
        """获取笔记纯文本内容"""
        body = {
            "note_id": note_id,
            "target_content_format": 0,  # PLAINTEXT
        }
        data = self._post("/openapi/note/v1/get_doc_content", body)
        if data:
            return data.get("content", "")
        return ""

    # ---------- 滚动笔记(追加模式) ----------

    ROLLING_NOTE_TITLE = "Nexus经验日志"
    _rolling_note_id_cache = None  # 内存缓存

    def _get_rolling_note_id(self):
        """获取滚动经验笔记的 note_id, 没有则创建"""
        if self._rolling_note_id_cache:
            return self._rolling_note_id_cache

        # 1. 先从本地缓存文件读
        cache_path = os.path.join("data", "ima_rolling_note.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                nid = cached.get("note_id")
                if nid:
                    self._rolling_note_id_cache = nid
                    return nid
            except Exception:
                pass

        # 2. 搜索已有笔记
        results = self.search_note(self.ROLLING_NOTE_TITLE, search_content=False)
        for r in results:
            if r.get("title") == self.ROLLING_NOTE_TITLE:
                nid = r.get("note_id")
                self._rolling_note_id_cache = nid
                self._save_rolling_note_id(nid)
                return nid

        # 3. 创建新笔记
        init_content = f"# {self.ROLLING_NOTE_TITLE}\n\n> Nexus 双核 Agent 经验自动同步日志, 按时间追加\n\n---\n"
        nid = self.import_doc(init_content, title=None)
        if nid:
            self._rolling_note_id_cache = nid
            self._save_rolling_note_id(nid)
            logger.info(f"IMA: 创建滚动经验笔记 note_id={nid}")
        return nid

    @staticmethod
    def _save_rolling_note_id(note_id):
        """保存滚动笔记ID到本地缓存"""
        try:
            os.makedirs("data", exist_ok=True)
            with open(os.path.join("data", "ima_rolling_note.json"), "w", encoding="utf-8") as f:
                json.dump({"note_id": note_id, "created": datetime.now().isoformat()}, f, ensure_ascii=False)
        except Exception:
            pass

    # ---------- 同步 ----------

    def sync_experience(self, exp_id, content, task_summary=""):
        """
        同步一条经验到 IMA（追加模式, 异步调用，不阻塞主流程）
        所有经验追加到同一个滚动笔记 'Nexus经验日志', 不再每次创建新笔记
        """
        if not self.enabled:
            return

        def _do_sync():
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                # 追加内容: 日期分隔 + 经验标题 + 正文
                entry = (
                    f"\n\n## [{timestamp}] 经验#{exp_id}\n"
                    f"**任务**: {task_summary[:80] if task_summary else '未命名'}\n\n"
                    f"{content}\n"
                    f"\n---"
                )
                note_id = self._get_rolling_note_id()
                if note_id:
                    ok = self.append_doc(note_id, entry)
                    if ok:
                        logger.info(f"IMA: 经验#{exp_id} 已追加到滚动笔记 note_id={note_id}")
                    else:
                        logger.warning(f"IMA: 经验#{exp_id} 追加失败, 尝试新建笔记")
                        # 追加失败兜底: 创建独立笔记
                        fallback = f"> 同步时间: {timestamp}\n> 经验ID: #{exp_id}\n\n{content}"
                        self.import_doc(fallback, title=f"[经验#{exp_id}] {task_summary[:50] if task_summary else ''}")
                else:
                    logger.warning(f"IMA: 获取滚动笔记失败, 经验#{exp_id} 未同步")
            except Exception as e:
                logger.warning(f"IMA: 同步异常: {e}")

        thread = threading.Thread(target=_do_sync, daemon=True)
        thread.start()


# 全局单例
_ima_client = None

def get_ima_client():
    global _ima_client
    if _ima_client is None:
        _ima_client = IMAClient()
    return _ima_client
