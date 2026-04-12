"""Unified memory facade — short-term, long-term, episodic, semantic retrieval, auto-summarization."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import ollama

from config import get_settings
from config.settings import ollama_runtime_options
from core.embeddings import EmbeddingEngine, SearchResult
from core.memory import Memory
from core.memory_scopes import (
    MemoryScope,
    classify_memory_scope,
    list_scoped_memories as list_scoped_memory_keys,
    load_scoped_memory,
    save_scoped_memory,
)

logger = logging.getLogger(__name__)

_SESSION_SUMMARY_PROMPT = """\
Analyze this conversation and extract structured information.
Reply with ONLY valid JSON (no markdown fences, no preamble).
Use Hebrew for content if the conversation was in Hebrew.

Required JSON schema:
{
  "summary": "2-3 sentence summary of what happened",
  "topics": ["topic1", "topic2"],
  "decisions": ["decision1"],
  "preferences": ["prefers X over Y"],
  "intentions": ["wants to do X but hasn't yet"],
  "action_items": ["pending task"]
}

If a field has no entries, use an empty list [].
"""


@dataclass
class Decision:
    id: int
    question: str
    decision: str
    reasoning: str
    source_conversation_id: int | None
    created_at: float

    @property
    def citation(self) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created_at))
        src = f", conversation #{self.source_conversation_id}" if self.source_conversation_id else ""
        return f"[source: {ts}{src}]"


@dataclass
class MemoryResult:
    content: str
    source_type: str
    score: float
    citation: str
    source_id: int = 0


class MemoryManager:
    def __init__(self, memory: Memory, embeddings: EmbeddingEngine):
        self.memory = memory
        self.embeddings = embeddings
        self.settings = get_settings()
        self._conversation_cache: list[dict] = []
        self.__client = None
        self._session_id: str | None = None
        self._session_started: bool = False

    @staticmethod
    def _normalize_scope(
        scope: MemoryScope | str | None,
        default: MemoryScope,
    ) -> MemoryScope:
        if isinstance(scope, MemoryScope):
            return scope
        if isinstance(scope, str) and scope.strip():
            try:
                return MemoryScope(scope.strip().lower())
            except ValueError:
                logger.warning("Unknown memory scope '%s'; using %s", scope, default.value)
        return default

    @staticmethod
    def _format_scoped_note(
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        lines = [f"# {title}", ""]
        lines.append(f"saved_at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        for key, value in (metadata or {}).items():
            if value in (None, "", [], {}):
                continue
            if isinstance(value, (dict, list)):
                value_text = json.dumps(value, ensure_ascii=False)
            else:
                value_text = str(value)
            lines.append(f"{key}: {value_text}")
        lines.extend(("", content.strip()))
        return "\n".join(lines).strip() + "\n"

    def _persist_scoped_record(
        self,
        key: str,
        title: str,
        content: str,
        scope: MemoryScope,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        note = self._format_scoped_note(title, content, metadata)
        save_scoped_memory(key, note, scope=scope)
        return scope.value

    @property
    def _client(self):
        if self.__client is None:
            self.__client = ollama.AsyncClient(host=self.settings.ollama_host)
        return self.__client

    # ── Session lifecycle ──

    def start_session(self) -> str:
        """Begin a new episodic session. Returns the session_id."""
        self._session_id = uuid.uuid4().hex[:12]
        self._session_started = True
        logger.info("Episodic session started: %s", self._session_id)
        return self._session_id

    @property
    def session_active(self) -> bool:
        return self._session_started

    async def end_session(self, conversation: list[dict]) -> dict | None:
        """Summarize the conversation, extract preferences/intentions, store as episodic memories."""
        if not conversation or len(conversation) < 2:
            self._session_started = False
            return None

        text_block = "\n".join(
            f"{m['role']}: {m['content']}"
            for m in conversation
            if m.get("content")
        )
        if len(text_block) < 40:
            self._session_started = False
            return None

        try:
            kw: dict[str, Any] = {
                "model": self.settings.ollama_model,
                "messages": [
                    {"role": "system", "content": _SESSION_SUMMARY_PROMPT},
                    {"role": "user", "content": text_block},
                ],
            }
            oopts = ollama_runtime_options(self.settings)
            if oopts:
                kw["options"] = oopts
            response = await self._client.chat(**kw)
            raw = response.message.content.strip()
            # Strip markdown code fences if the model wraps its reply
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            extracted = json.loads(raw)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Session summary extraction failed: %s", e)
            self._session_started = False
            return None

        summary_text: str = extracted.get("summary", "")
        topics: list[str] = extracted.get("topics", [])
        preferences: list[str] = extracted.get("preferences", [])
        intentions: list[str] = extracted.get("intentions", [])

        meta = {
            "session_id": self._session_id,
            "topics": topics,
            "decisions": extracted.get("decisions", []),
            "action_items": extracted.get("action_items", []),
        }

        if summary_text:
            summary_scope = MemoryScope.LOCAL
            summary_meta = dict(meta)
            summary_meta["scope"] = summary_scope.value
            mem_id = await self.memory.add_episodic_memory(
                "session_summary", summary_text, summary_meta,
            )
            await self.embeddings.embed_and_store(summary_text, "episodic", mem_id)
            self._persist_scoped_record(
                f"session-{self._session_id or 'unknown'}-summary",
                "Session summary",
                summary_text,
                summary_scope,
                {"memory_id": mem_id, **summary_meta},
            )
            logger.info("Session summary stored (id=%d, %d chars)", mem_id, len(summary_text))

        for pref in preferences:
            if pref:
                pref_scope = MemoryScope.USER
                pref_id = await self.memory.add_episodic_memory(
                    "preference", pref, {"session_id": self._session_id, "scope": pref_scope.value},
                )
                await self.embeddings.embed_and_store(pref, "episodic", pref_id)
                self._persist_scoped_record(
                    f"preference-{pref_id}",
                    "Preference",
                    pref,
                    pref_scope,
                    {"memory_id": pref_id, "session_id": self._session_id},
                )
                await self.set_fact(f"preference:{pref[:60]}", pref)

        for intention in intentions:
            if intention:
                intention_scope = MemoryScope.PROJECT
                int_id = await self.memory.add_episodic_memory(
                    "intention", intention, {"session_id": self._session_id, "scope": intention_scope.value},
                )
                await self.embeddings.embed_and_store(intention, "episodic", int_id)
                self._persist_scoped_record(
                    f"intention-{int_id}",
                    "Intention",
                    intention,
                    intention_scope,
                    {"memory_id": int_id, "session_id": self._session_id},
                )

        self._session_started = False
        logger.info(
            "Session %s ended — %d preferences, %d intentions extracted",
            self._session_id, len(preferences), len(intentions),
        )
        return extracted

    # ── Short-term memory ──

    async def add_message(self, role: str, content: str):
        await self.memory.add_message(role, content)
        self._conversation_cache.append({"role": role, "content": content})
        if len(self._conversation_cache) > 50:
            self._conversation_cache = self._conversation_cache[-40:]

        if role == "assistant" and len(content) > 20:
            cursor = await self.memory._db.execute(
                "SELECT MAX(id) FROM conversations"
            )
            row = await cursor.fetchone()
            msg_id = row[0] if row else 0
            await self.embeddings.embed_and_store(content, "conversation", msg_id)

    async def get_context_window(self, limit: int = 20) -> list[dict]:
        if self._conversation_cache:
            return self._conversation_cache[-limit:]
        return await self.memory.get_recent_messages(limit)

    # ── Long-term memory (facts + decisions) ──

    async def set_fact(self, key: str, value: str):
        await self.memory.set_fact(key, value)
        cursor = await self.memory._db.execute(
            "SELECT id FROM facts WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        if row:
            await self.embeddings.embed_and_store(f"{key}: {value}", "fact", row[0])
        fact_scope = classify_memory_scope(key)
        self._persist_scoped_record(
            f"fact-{key}",
            f"Fact: {key}",
            value,
            fact_scope,
            {"key": key, "scope": fact_scope.value},
        )

    async def get_all_facts(self) -> dict[str, str]:
        return await self.memory.get_all_facts()

    async def record_decision(
        self,
        question: str,
        decision: str,
        reasoning: str = "",
        source_conversation_id: int | None = None,
    ):
        db = self.memory._db
        await db.execute(
            """INSERT INTO decisions (question, decision, reasoning, source_conversation_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (question, decision, reasoning, source_conversation_id, time.time()),
        )
        await db.commit()

        cursor = await db.execute("SELECT last_insert_rowid()")
        row = await cursor.fetchone()
        dec_id = row[0] if row else 0
        await self.embeddings.embed_and_store(
            f"Q: {question} | Decision: {decision} | Reason: {reasoning}",
            "decision",
            dec_id,
        )

    async def recall_decisions(self, topic: str, top_k: int = 5) -> list[Decision]:
        results = await self.embeddings.search(topic, top_k=top_k * 2)
        decision_ids = [r.source_id for r in results if r.source_type == "decision"][:top_k]
        if not decision_ids:
            return []

        placeholders = ",".join("?" for _ in decision_ids)
        cursor = await self.memory._db.execute(
            f"SELECT id, question, decision, reasoning, source_conversation_id, created_at "
            f"FROM decisions WHERE id IN ({placeholders})",
            decision_ids,
        )
        rows = await cursor.fetchall()
        return [
            Decision(id=r[0], question=r[1], decision=r[2], reasoning=r[3],
                     source_conversation_id=r[4], created_at=r[5])
            for r in rows
        ]

    # ── Episodic memory (manual + recall) ──

    async def store_manual_memory(
        self,
        content: str,
        scope: MemoryScope | str | None = None,
    ) -> int:
        """Store a user-requested memory ('remember that...')."""
        chosen_scope = self._normalize_scope(scope, MemoryScope.USER)
        mem_id = await self.memory.add_episodic_memory(
            "manual", content, {"session_id": self._session_id, "scope": chosen_scope.value},
        )
        await self.embeddings.embed_and_store(content, "episodic", mem_id)
        self._persist_scoped_record(
            f"memory-{mem_id}",
            "Manual memory",
            content,
            chosen_scope,
            {"memory_id": mem_id, "session_id": self._session_id, "scope": chosen_scope.value},
        )
        logger.info("Manual memory stored (id=%d): %.80s", mem_id, content)
        return mem_id

    async def get_episodes(self, limit: int = 20) -> list[dict]:
        """Return recent episodic memories for dashboard/API consumers."""
        return await self.memory.get_episodic_memories(limit=limit)

    def list_scoped_memories(self, scope: MemoryScope | str = MemoryScope.PROJECT) -> list[str]:
        resolved = self._normalize_scope(scope, MemoryScope.PROJECT)
        return list_scoped_memory_keys(resolved)

    def read_scoped_memory(
        self,
        key: str,
        scope: MemoryScope | str | None = None,
    ) -> str | None:
        if scope is None:
            for candidate in (MemoryScope.USER, MemoryScope.PROJECT, MemoryScope.LOCAL):
                content = load_scoped_memory(key, candidate)
                if content is not None:
                    return content
            return None
        resolved = self._normalize_scope(scope, MemoryScope.PROJECT)
        return load_scoped_memory(key, resolved)

    async def recall_episodic(self, query: str, top_k: int = 5) -> list[MemoryResult]:
        """Semantic search scoped to episodic memories."""
        results = await self.embeddings.search(query, top_k=top_k * 2)
        episodic = [r for r in results if r.source_type == "episodic"][:top_k]
        out: list[MemoryResult] = []
        for r in episodic:
            await self.memory.update_recall_stats(r.source_id)
            ts_str = time.strftime("%Y-%m-%d", time.localtime(time.time()))
            out.append(MemoryResult(
                content=r.content,
                source_type=r.source_type,
                score=r.score,
                citation=f"[episodic #{r.source_id}, score={r.score:.2f}]",
                source_id=r.source_id,
            ))
        return out

    async def get_session_context(self, query: str, top_k: int = 5) -> str:
        """Returns formatted episodic memories for the system prompt."""
        results = await self.recall_episodic(query, top_k=top_k)
        if not results:
            return ""
        lines = ["Episodic memories (past sessions):"]
        for r in results:
            lines.append(f"  - {r.content} {r.citation}")
        return "\n".join(lines)

    # ── Semantic recall (general) ──

    async def recall(self, query: str, top_k: int = 5) -> list[MemoryResult]:
        results = await self.embeddings.search(query, top_k=top_k)
        return [
            MemoryResult(
                content=r.content,
                source_type=r.source_type,
                score=r.score,
                citation=f"[{r.source_type} #{r.source_id}, score={r.score:.2f}]",
                source_id=r.source_id,
            )
            for r in results
        ]

    async def get_relevant_history(self, query: str, top_k: int = 3) -> str:
        """Returns formatted text with citations for the system prompt."""
        results = await self.recall(query, top_k=top_k)
        if not results:
            return ""
        lines = ["Relevant past context:"]
        for r in results:
            lines.append(f"  - {r.content} {r.citation}")
        return "\n".join(lines)

    # ── Auto-summarization ──

    async def maybe_summarize(self, conversation: list[dict]) -> str | None:
        """If conversation is long enough, summarize older messages."""
        threshold = self.settings.summarize_threshold
        if len(conversation) < threshold:
            return None

        to_summarize = conversation[: len(conversation) - 15]
        text_block = "\n".join(
            f"{m['role']}: {m['content']}" for m in to_summarize if m.get("content")
        )

        try:
            kw: dict[str, Any] = {
                "model": self.settings.ollama_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Summarize the following conversation concisely, preserving key decisions, facts, and action items. Reply with ONLY the summary, no preamble.",
                    },
                    {"role": "user", "content": text_block},
                ],
            }
            oopts = ollama_runtime_options(self.settings)
            if oopts:
                kw["options"] = oopts
            response = await self._client.chat(**kw)
            summary = response.message.content.strip()
        except Exception as e:
            logger.warning("Summarization failed: %s", e)
            return None

        db = self.memory._db
        first_id = None
        last_id = None
        cursor = await db.execute(
            "SELECT MIN(id), MAX(id) FROM conversations ORDER BY id LIMIT ?",
            (len(to_summarize),),
        )
        row = await cursor.fetchone()
        if row:
            first_id, last_id = row

        await db.execute(
            """INSERT INTO summaries (conversation_start_id, conversation_end_id, summary, created_at)
               VALUES (?, ?, ?, ?)""",
            (first_id, last_id, summary, time.time()),
        )
        await db.commit()

        logger.info("Summarized %d messages into %d chars", len(to_summarize), len(summary))
        return summary
