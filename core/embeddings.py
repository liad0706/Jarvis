"""Embedding engine — generates and searches vector embeddings via Ollama."""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
import ollama

from config import get_settings
from config.settings import ollama_runtime_options

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "memory.db"


@dataclass
class SearchResult:
    source_type: str
    source_id: int
    content: str
    score: float


def _serialize_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _deserialize_embedding(data: bytes) -> list[float]:
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingEngine:
    def __init__(self, db: aiosqlite.Connection | None = None):
        self.settings = get_settings()
        self.model = self.settings.embedding_model
        self._db = db
        self.__client = None
        self._embed_model_resolved: str | None = None
        self._warned_once: bool = False
        self._disabled: bool = False

    @property
    def _client(self):
        if self.__client is None:
            self.__client = ollama.AsyncClient(host=self.settings.ollama_host)
        return self.__client

    def _embedding_model_candidates(self) -> list[str]:
        """Ollama sometimes resolves :latest differently; try several names."""
        primary = (self.settings.embedding_model or "").strip()
        alts = [
            primary,
            "nomic-embed-text",
            "nomic-embed-text:latest",
        ]
        seen: set[str] = set()
        out: list[str] = []
        for m in alts:
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out

    async def set_db(self, db: aiosqlite.Connection):
        self._db = db

    async def embed(self, text: str) -> list[float]:
        """Generate embedding vector for text."""
        # If we already know embeddings are unavailable, skip silently
        if self._disabled:
            return []

        models = (
            [self._embed_model_resolved]
            if self._embed_model_resolved
            else self._embedding_model_candidates()
        )
        last_err: Exception | None = None
        for model in models:
            try:
                ekw: dict = {"model": model, "input": text}
                oopts = ollama_runtime_options(self.settings)
                if oopts:
                    ekw["options"] = oopts
                response = await self._client.embed(**ekw)
                if hasattr(response, "embeddings") and response.embeddings:
                    vec = response.embeddings[0]
                else:
                    vec = response.embedding if hasattr(response, "embedding") else []
                if vec:
                    if self._embed_model_resolved is None and model != self.settings.embedding_model:
                        logger.info(
                            "Embeddings: using %r (configured %r was not found on Ollama)",
                            model,
                            self.settings.embedding_model,
                        )
                    self._embed_model_resolved = model
                    return vec
            except Exception as e:
                last_err = e
                continue
        if last_err and not self._warned_once:
            self._warned_once = True
            logger.warning(
                "Embedding model not available (tried %s): %s — "
                "run: ollama pull nomic-embed-text  (further warnings suppressed)",
                models,
                last_err,
            )
            # Disable further attempts to avoid repeated network calls
            self._disabled = True
        return []

    async def embed_and_store(
        self, content: str, source_type: str, source_id: int
    ) -> bool:
        """Generate embedding and store it in the database."""
        vec = await self.embed(content)
        if not vec:
            return False

        import time
        blob = _serialize_embedding(vec)
        await self._db.execute(
            """INSERT INTO embeddings (source_type, source_id, content, embedding, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (source_type, source_id, content[:500], blob, time.time()),
        )
        await self._db.commit()
        return True

    async def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Search stored embeddings by cosine similarity."""
        query_vec = await self.embed(query)
        if not query_vec:
            return []

        cursor = await self._db.execute(
            "SELECT id, source_type, source_id, content, embedding FROM embeddings"
        )
        rows = await cursor.fetchall()

        scored = []
        for row in rows:
            stored_vec = _deserialize_embedding(row[4])
            score = _cosine_similarity(query_vec, stored_vec)
            scored.append(SearchResult(
                source_type=row[1],
                source_id=row[2],
                content=row[3],
                score=score,
            ))

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]
