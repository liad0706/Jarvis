"""FAISS-based vector memory backend — fast approximate nearest-neighbor search.

Replaces brute-force cosine similarity in EmbeddingEngine.search() with
FAISS IndexFlatIP (inner-product on L2-normalized vectors ≡ cosine similarity).
Falls back to the original brute-force method if faiss is not installed.

Hybrid search combines FAISS (semantic) + BM25 (keyword) via Reciprocal Rank Fusion.
"""

from __future__ import annotations

import logging
import math
import pickle
import re
import struct
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_FAISS_INDEX_PATH = _DATA_DIR / "faiss_index.bin"
_FAISS_META_PATH = _DATA_DIR / "faiss_meta.pkl"

# ── Try importing faiss ──────────────────────────────────────────────
try:
    import numpy as np
    import faiss

    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    logger.info("faiss-cpu not installed — using brute-force fallback for vector search")


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class VectorResult:
    source_type: str
    source_id: int
    content: str
    score: float


@dataclass
class _DocMeta:
    db_id: int
    source_type: str
    source_id: int
    content: str


# ── BM25 sparse index ───────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-zA-Z0-9\u0590-\u05FF]+")


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


class BM25Index:
    """Minimal BM25 implementation (no external deps)."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._docs: list[tuple[int, list[str]]] = []  # (doc_idx, tokens)
        self._df: dict[str, int] = defaultdict(int)
        self._avgdl: float = 0.0
        self._n: int = 0

    def add(self, doc_idx: int, text: str):
        tokens = _tokenize(text)
        self._docs.append((doc_idx, tokens))
        seen = set()
        for t in tokens:
            if t not in seen:
                self._df[t] += 1
                seen.add(t)
        self._n += 1
        total = sum(len(d[1]) for d in self._docs)
        self._avgdl = total / self._n if self._n else 1.0

    def search(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        scores: dict[int, float] = defaultdict(float)
        for doc_idx, tokens in self._docs:
            dl = len(tokens)
            tf_map: dict[str, int] = defaultdict(int)
            for t in tokens:
                tf_map[t] += 1
            for qt in q_tokens:
                if qt not in tf_map:
                    continue
                tf = tf_map[qt]
                df = self._df.get(qt, 0)
                idf = math.log((self._n - df + 0.5) / (df + 0.5) + 1.0)
                num = tf * (self.k1 + 1)
                den = tf + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
                scores[doc_idx] += idf * num / den
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]


# ── FAISS index wrapper ─────────────────────────────────────────────

class FAISSIndex:
    """Wraps a FAISS IndexFlatIP for cosine similarity on L2-normed vectors."""

    def __init__(self, dim: int = 768):
        self.dim = dim
        self._index: Any = None
        self._metas: list[_DocMeta] = []
        if FAISS_AVAILABLE:
            self._index = faiss.IndexFlatIP(dim)

    @property
    def ready(self) -> bool:
        return self._index is not None

    @property
    def count(self) -> int:
        return len(self._metas)

    def add(self, vector: list[float], meta: _DocMeta):
        if not self.ready:
            return
        vec = np.array(vector, dtype=np.float32).reshape(1, -1)
        faiss.normalize_L2(vec)
        self._index.add(vec)
        self._metas.append(meta)

    def save_to_disk(self) -> None:
        """Persist the FAISS index and metadata to disk for fast startup."""
        if not self.ready or not self._metas:
            return
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            faiss.write_index(self._index, str(_FAISS_INDEX_PATH))
            _FAISS_META_PATH.write_bytes(pickle.dumps(self._metas))
            logger.debug("FAISS index saved to disk (%d docs)", len(self._metas))
        except Exception as e:
            logger.warning("FAISS save_to_disk failed: %s", e)

    @classmethod
    def load_from_disk(cls, dim: int = 768) -> "FAISSIndex | None":
        """Load a previously saved FAISS index from disk. Returns None if not found."""
        if not FAISS_AVAILABLE:
            return None
        if not _FAISS_INDEX_PATH.exists() or not _FAISS_META_PATH.exists():
            return None
        try:
            obj = cls(dim=dim)
            obj._index = faiss.read_index(str(_FAISS_INDEX_PATH))
            obj._metas = pickle.loads(_FAISS_META_PATH.read_bytes())
            logger.info("FAISS index loaded from disk (%d docs)", len(obj._metas))
            return obj
        except Exception as e:
            logger.warning("FAISS load_from_disk failed: %s — will rebuild", e)
            return None

    def search(self, query_vec: list[float], top_k: int = 10) -> list[VectorResult]:
        if not self.ready or not self._metas:
            return []
        qvec = np.array(query_vec, dtype=np.float32).reshape(1, -1)
        faiss.normalize_L2(qvec)
        k = min(top_k, len(self._metas))
        scores, indices = self._index.search(qvec, k)
        results = []
        for i in range(k):
            idx = int(indices[0][i])
            if idx < 0:
                continue
            m = self._metas[idx]
            results.append(VectorResult(
                source_type=m.source_type,
                source_id=m.source_id,
                content=m.content,
                score=float(scores[0][i]),
            ))
        return results


# ── Hybrid search (RRF) ─────────────────────────────────────────────

def reciprocal_rank_fusion(
    *ranked_lists: list[tuple[int, float]],
    k: int = 60,
) -> list[tuple[int, float]]:
    """Merge multiple ranked lists via Reciprocal Rank Fusion (RRF).

    Each ranked list is [(doc_idx, score), ...].
    Returns merged [(doc_idx, rrf_score), ...] sorted descending.
    """
    scores: dict[int, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, (doc_idx, _score) in enumerate(ranked):
            scores[doc_idx] += 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


class HybridMemoryBackend:
    """Combines FAISS (dense) + BM25 (sparse) search with RRF fusion.

    Drop-in enhancement for MemoryManager — call build_index() on startup,
    then use hybrid_search() instead of plain embedding search.
    """

    def __init__(self, dim: int = 768):
        self.faiss_index = FAISSIndex(dim=dim)
        self.bm25 = BM25Index()
        self._idx_to_meta: dict[int, _DocMeta] = {}
        self._next_idx = 0

    @property
    def ready(self) -> bool:
        return self.faiss_index.ready

    def add_document(
        self,
        vector: list[float],
        content: str,
        source_type: str,
        source_id: int,
        db_id: int = 0,
    ):
        """Add a document to both FAISS and BM25 indexes."""
        idx = self._next_idx
        self._next_idx += 1
        meta = _DocMeta(db_id=db_id, source_type=source_type, source_id=source_id, content=content)
        self._idx_to_meta[idx] = meta

        if vector and self.faiss_index.ready:
            self.faiss_index.add(vector, meta)

        self.bm25.add(idx, content)

    def hybrid_search(
        self,
        query: str,
        query_vec: list[float] | None = None,
        top_k: int = 10,
        semantic_weight: float = 0.7,
    ) -> list[VectorResult]:
        """Search using both FAISS and BM25, merged via RRF."""
        fetch_k = top_k * 3

        # Dense (FAISS) results
        faiss_ranked: list[tuple[int, float]] = []
        if query_vec and self.faiss_index.ready:
            faiss_results = self.faiss_index.search(query_vec, fetch_k)
            # Map back to idx
            for r in faiss_results:
                for idx, m in self._idx_to_meta.items():
                    if m.source_id == r.source_id and m.source_type == r.source_type:
                        faiss_ranked.append((idx, r.score))
                        break

        # Sparse (BM25) results
        bm25_ranked = self.bm25.search(query, fetch_k)

        # Merge via RRF
        if faiss_ranked and bm25_ranked:
            merged = reciprocal_rank_fusion(faiss_ranked, bm25_ranked)
        elif faiss_ranked:
            merged = [(idx, score) for idx, score in faiss_ranked]
        elif bm25_ranked:
            merged = bm25_ranked
        else:
            return []

        results = []
        for idx, rrf_score in merged[:top_k]:
            meta = self._idx_to_meta.get(idx)
            if meta:
                results.append(VectorResult(
                    source_type=meta.source_type,
                    source_id=meta.source_id,
                    content=meta.content,
                    score=rrf_score,
                ))
        return results

    async def build_from_db(self, db, embedding_engine) -> int:
        """Load all existing embeddings from SQLite into FAISS + BM25.

        On startup, tries to load the FAISS index from disk cache first.
        Falls back to a full rebuild if the cache is missing or stale.
        BM25 is always rebuilt (pure Python, fast).
        Returns number of documents indexed.
        """
        from core.embeddings import _deserialize_embedding

        # Count rows in DB to detect if cache is stale
        count_cursor = await db.execute("SELECT COUNT(*) FROM embeddings")
        row = await count_cursor.fetchone()
        db_count = row[0] if row else 0

        # Try loading FAISS from disk if row count matches
        cached = FAISSIndex.load_from_disk(dim=self.faiss_index.dim)
        use_cache = cached is not None and cached.count == db_count and db_count > 0

        if use_cache:
            self.faiss_index = cached
            # Rebuild BM25 (no disk format — fetch content only, no embedding BLOB)
            content_cursor = await db.execute(
                "SELECT id, source_type, source_id, content FROM embeddings"
            )
            rows = await content_cursor.fetchall()
            for db_id, source_type, source_id, content in rows:
                idx = self._next_idx
                self._next_idx += 1
                meta = _DocMeta(db_id=db_id, source_type=source_type, source_id=source_id, content=content or "")
                self._idx_to_meta[idx] = meta
                self.bm25.add(idx, content or "")
            logger.info("HybridMemoryBackend: loaded %d docs from cache (FAISS disk hit)", db_count)
            return db_count

        # Full rebuild
        cursor = await db.execute(
            "SELECT id, source_type, source_id, content, embedding FROM embeddings"
        )
        rows = await cursor.fetchall()
        count = 0
        for row in rows:
            db_id, source_type, source_id, content, emb_blob = row
            vec = _deserialize_embedding(emb_blob) if emb_blob else []
            self.add_document(
                vector=vec,
                content=content,
                source_type=source_type,
                source_id=source_id,
                db_id=db_id,
            )
            count += 1

        # Save to disk for next startup
        self.faiss_index.save_to_disk()
        logger.info("HybridMemoryBackend: indexed %d documents (FAISS=%s, saved to cache)", count, self.faiss_index.ready)
        return count
