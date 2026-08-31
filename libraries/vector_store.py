"""
向量存储 —— 对应视频中的"高维向量模式搜索"和"标点定位"
优先用 embedding API, 不可用时回退到 TF-IDF 关键词匹配
"""
import json
import math
import os
from pathlib import Path
from collections import Counter
from config import DATA_DIR, EMBEDDING_CONFIG

VECTOR_FILE = DATA_DIR / "vectors.json"


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

    def _load(self):
        if VECTOR_FILE.exists():
            data = json.loads(VECTOR_FILE.read_text(encoding="utf-8"))
            self.vectors = data.get("vectors", {})
            self.documents = data.get("documents", {})
            self.idf = data.get("idf", {})

    def _save(self):
        VECTOR_FILE.write_text(
            json.dumps({
                "vectors": self.vectors,
                "documents": self.documents,
                "idf": self.idf,
            }, ensure_ascii=False),
            encoding="utf-8",
        )

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
        nb = math.sqrt(sum(x * x for x in b))
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

    def _recompute_idf(self):
        n_docs = len(self.documents) or 1
        df: dict[str, int] = {}
        for text in self.documents.values():
            for w in set(self._tokenize(text)):
                df[w] = df.get(w, 0) + 1
        self.idf = {w: math.log((n_docs + 1) / (c + 1)) + 1
                    for w, c in df.items()}

    def add(self, doc_id: str, text: str):
        self.documents[doc_id] = text
        vec = self._embed(text)
        if vec:
            self.vectors[doc_id] = vec
        self._recompute_idf()
        self._save()

    def delete(self, doc_id: str):
        self.documents.pop(doc_id, None)
        self.vectors.pop(doc_id, None)
        self._recompute_idf()
        self._save()

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        if not self.documents:
            return []

        if self._mode == "embedding" and self.vectors:
            q_vec = self._embed(query)
            if q_vec:
                scored = [
                    (doc_id, self._cosine_sim(q_vec, vec))
                    for doc_id, vec in self.vectors.items()
                ]
                scored.sort(key=lambda x: x[1], reverse=True)
                return scored[:top_k]

        q_vec = self._tfidf_vector(query)
        scored = []
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

    @property
    def mode(self) -> str:
        return self._mode

    def __len__(self):
        return len(self.documents)
