"""
向量存储 —— 对应视频中的"高维向量模式搜索"和"标点定位"
优先用 embedding API, 不可用时回退到 TF-IDF 关键词匹配

性能策略:
- 写盘防抖: add/delete 只更新内存并标记 dirty, 500ms 内的多次变更合并一次落盘
- IDF 懒重算: 批量增删时不逐条重算, 只在下次 search 前重算一次
- bulk 模式: 启动批量加载时 begin_bulk/end_bulk, 整批只落盘一次
- 进程退出时 atexit 兜底 flush, 保证不丢数据
"""
import atexit
import json
import math
import threading
from collections import Counter
from config import DATA_DIR, EMBEDDING_CONFIG

VECTOR_FILE = DATA_DIR / "vectors.json"
_SAVE_DEBOUNCE_SECONDS = 0.5


class VectorStore:
    """
    轻量向量库, 支持两种模式:
    1. embedding 模式: 调用 API 生成向量, 余弦相似度检索 (标点定位)
    2. fallback 模式: TF-IDF 关键词检索 (一页一页翻的简化版)
    """

    def __init__(self):
        self.vectors: dict[str, list[float]] = {}
        self.documents: dict[str, str] = {}
        self.idf: dict[str, float] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self._save_timer: threading.Timer | None = None
        self._bulk_depth = 0
        self._idf_dirty = False
        self._load()
        self._client = None
        self._mode = "tfidf"
        if EMBEDDING_CONFIG["enabled"] and EMBEDDING_CONFIG["api_key"]:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    base_url=EMBEDDING_CONFIG["base_url"],
                    api_key=EMBEDDING_CONFIG["api_key"],
                )
                self._mode = "embedding"
            except Exception:
                self._mode = "tfidf"
        atexit.register(self.flush)

    def _load(self):
        if VECTOR_FILE.exists():
            data = json.loads(VECTOR_FILE.read_text(encoding="utf-8"))
            self.vectors = data.get("vectors", {})
            self.documents = data.get("documents", {})
            self.idf = data.get("idf", {})

    # ---------- 落盘(防抖) ----------
    def _schedule_save(self):
        if self._bulk_depth > 0:
            self._dirty = True
            return
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(
                _SAVE_DEBOUNCE_SECONDS, self.flush
            )
            self._save_timer.daemon = True
            self._save_timer.start()

    def flush(self):
        """立即把内存状态落盘 (Timer 回调 / atexit / 手动调用)"""
        with self._lock:
            if not self._dirty:
                return
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
            VECTOR_FILE.write_text(
                json.dumps({
                    "vectors": self.vectors,
                    "documents": self.documents,
                    "idf": self.idf,
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            self._dirty = False

    # ---------- 批量加载 ----------
    def begin_bulk(self):
        """进入批量模式: 暂停防抖落盘与 IDF 重算"""
        with self._lock:
            self._bulk_depth += 1

    def end_bulk(self):
        """结束批量模式: 统一重算 IDF 并落盘一次"""
        with self._lock:
            self._bulk_depth = max(0, self._bulk_depth - 1)
            if self._bulk_depth == 0:
                self._idf_dirty = True
                self._dirty = True
                self._recompute_idf_locked()
                self.flush()

    def _embed(self, text: str) -> list[float]:
        if self._mode != "embedding" or self._client is None:
            return []
        try:
            resp = self._client.embeddings.create(
                model=EMBEDDING_CONFIG["model"],
                input=text[:8000],
            )
            return resp.data[0].embedding
        except Exception as e:
            print(f"[embedding] 调用失败, 回退 TF-IDF: {e}")
            self._mode = "tfidf"
            return []

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens = []
        for word in text.lower().replace("\n", " ").split():
            if any('\u4e00' <= c <= '\u9fff' for c in word):
                tokens.extend(list(word))
            else:
                tokens.append(word)
        return tokens

    def _tfidf_vector(self, text: str) -> dict[str, float]:
        tokens = self._tokenize(text)
        tf = Counter(tokens)
        total = len(tokens) or 1
        return {w: (c / total) * self.idf.get(w, 0.0) for w, c in tf.items()}

    def _recompute_idf_locked(self):
        n_docs = len(self.documents) or 1
        df: dict[str, int] = {}
        for text in self.documents.values():
            for w in set(self._tokenize(text)):
                df[w] = df.get(w, 0) + 1
        self.idf = {w: math.log((n_docs + 1) / (c + 1)) + 1
                    for w, c in df.items()}
        self._idf_dirty = False

    def _ensure_idf(self):
        """搜索前按需重算一次 IDF (批量增删只触发一次)"""
        if self._idf_dirty:
            with self._lock:
                if self._idf_dirty:
                    self._recompute_idf_locked()

    def add(self, doc_id: str, text: str):
        with self._lock:
            self.documents[doc_id] = text
            vec = self._embed(text)
            if vec:
                self.vectors[doc_id] = vec
            self._idf_dirty = True
            self._dirty = True
            if self._bulk_depth == 0:
                self._recompute_idf_locked()
        self._schedule_save()

    def delete(self, doc_id: str):
        with self._lock:
            self.documents.pop(doc_id, None)
            self.vectors.pop(doc_id, None)
            self._idf_dirty = True
            self._dirty = True
            if self._bulk_depth == 0:
                self._recompute_idf_locked()
        self._schedule_save()

    def _semantic_search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """语义检索: embedding 余弦 / TF-IDF 余弦 (单路)"""
        if self._mode == "embedding" and self.vectors:
            q_vec = self._embed(query)
            if q_vec:
                with self._lock:
                    scored = [
                        (doc_id, self._cosine_sim(q_vec, vec))
                        for doc_id, vec in self.vectors.items()
                    ]
                scored.sort(key=lambda x: x[1], reverse=True)
                return scored[:top_k]

        q_vec = self._tfidf_vector(query)
        scored = []
        with self._lock:
            for doc_id, text in self.documents.items():
                d_vec = self._tfidf_vector(text)
                common = set(q_vec) & set(d_vec)
                dot = sum(q_vec[w] * d_vec[w] for w in common)
                na = math.sqrt(sum(v * v for v in q_vec.values()))
                nb = math.sqrt(sum(v * v for v in d_vec.values()))
                sim = dot / (na * nb) if na and nb else 0.0
                scored.append((doc_id, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def _keyword_search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """关键词检索: 查询词与文档词的 IDF 加权重叠 (精确匹配符号名/报错/术语)"""
        q_tokens = set(self._tokenize(query))
        if not q_tokens:
            return []
        scored = []
        with self._lock:
            for doc_id, text in self.documents.items():
                doc_tokens = set(self._tokenize(text))
                overlap = q_tokens & doc_tokens
                if not overlap:
                    continue
                score = sum(self.idf.get(t, 0.0) for t in overlap)
                scored.append((doc_id, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    @staticmethod
    def _rrf_fuse(rankings: list[list[tuple[str, float]]], k: int = 60) -> list[tuple[str, float]]:
        """Reciprocal Rank Fusion: 多路排名融合, 分数=sum(1/(k+rank)), rank 从 1 开始"""
        fused: dict[str, float] = {}
        for ranking in rankings:
            for rank, (doc_id, _) in enumerate(ranking):
                fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
        return sorted(fused.items(), key=lambda x: x[1], reverse=True)

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        """混合检索: 语义路 + 关键词路 RRF 融合, 提升精确术语/符号/报错的召回"""
        if not self.documents:
            return []
        self._ensure_idf()
        semantic = self._semantic_search(query, top_k * 2)
        keyword = self._keyword_search(query, top_k * 2)
        fused = self._rrf_fuse([semantic, keyword])
        return fused[:top_k]

    @property
    def mode(self) -> str:
        return self._mode

    def __len__(self):
        return len(self.documents)
