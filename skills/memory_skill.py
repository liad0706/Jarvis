"""Memory skill — lets the LLM store and recall episodic memories."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from core.memory_scopes import MemoryScope
from core.skill_base import BaseSkill

if TYPE_CHECKING:
    from core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


class MemorySkill(BaseSkill):
    name = "memory"
    description = (
        "Store and recall episodic memories, including user/project/local scoped notes. "
        "Use memory_remember when the user says "
        "'תזכור ש...' / 'remember that...' or when you decide something is worth "
        "remembering for future sessions. Use memory_recall to search past memories."
    )

    RISK_MAP = {
        "remember": "low",
        "recall": "low",
        "list_recent": "low",
        "list_scoped": "low",
        "read_scoped": "low",
        "forget": "medium",
    }

    def __init__(self, memory_manager: MemoryManager):
        self._mm = memory_manager

    @staticmethod
    def _validate_scope(scope: str, default: str = "user") -> str:
        scope_text = (scope or default).strip().lower()
        if scope_text not in {item.value for item in MemoryScope}:
            raise ValueError(f"Unknown scope '{scope}'. Use user, project, or local.")
        return scope_text

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("memory.%s failed", action)
            return {"error": str(e)}

    async def do_remember(self, content: str, scope: str = "user") -> dict:
        """Store something in long-term episodic memory. Use when the user says 'תזכור ש...' / 'remember that...' or when important context should be preserved across sessions."""
        scope_text = self._validate_scope(scope)
        mem_id = await self._mm.store_manual_memory(content, scope=scope_text)
        return {
            "status": "ok",
            "memory_id": mem_id,
            "scope": scope_text,
            "reply_to_user_hebrew": f"שמרתי בזיכרון: {content}",
        }

    async def do_recall(self, query: str, top_k: int = 5) -> dict:
        """Search episodic memories by semantic similarity. Returns the most relevant past memories for a given query."""
        top_k = int(top_k)
        results = await self._mm.recall_episodic(query, top_k=top_k)
        if not results:
            return {
                "status": "empty",
                "reply_to_user_hebrew": "לא מצאתי זיכרונות רלוונטיים.",
            }
        items = []
        for r in results:
            items.append({
                "content": r.content,
                "score": round(r.score, 3),
                "source_id": r.source_id,
            })
        return {
            "status": "ok",
            "count": len(items),
            "memories": items,
        }

    async def do_list_recent(self, limit: int = 10) -> dict:
        """List the most recent episodic memories (all types: manual, session summaries, preferences, intentions)."""
        limit = int(limit)
        memories = await self._mm.memory.get_episodic_memories(limit=limit)
        if not memories:
            return {
                "status": "empty",
                "reply_to_user_hebrew": "אין זיכרונות שמורים עדיין.",
            }
        items = []
        for m in memories:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["created_at"]))
            items.append({
                "id": m["id"],
                "type": m["memory_type"],
                "content": m["content"],
                "created_at": ts,
                "recalled_count": m["recalled_count"],
                "scope": (m.get("metadata") or {}).get("scope", ""),
            })
        return {
            "status": "ok",
            "count": len(items),
            "memories": items,
        }

    async def do_list_scoped(self, scope: str = "project") -> dict:
        """List scoped memory note files for one scope directory: user, project, or local."""
        scope_text = self._validate_scope(scope, default="project")
        keys = self._mm.list_scoped_memories(scope_text)
        return {
            "status": "ok" if keys else "empty",
            "scope": scope_text,
            "count": len(keys),
            "keys": keys,
        }

    async def do_read_scoped(self, key: str, scope: str = "") -> dict:
        """Read one scoped memory note by key. If scope is omitted, search all scopes."""
        scope_text = self._validate_scope(scope, default="project") if scope else ""
        content = self._mm.read_scoped_memory(key, scope=scope_text or None)
        if content is None:
            return {"error": f"Scoped memory '{key}' not found"}
        return {
            "status": "ok",
            "key": key,
            "scope": scope_text or "auto",
            "content": content,
        }

    async def do_forget(self, memory_id: int) -> dict:
        """Delete a specific episodic memory by its ID. Use for corrections or when the user asks to remove a memory."""
        memory_id = int(memory_id)
        deleted = await self._mm.memory.delete_episodic_memory(memory_id)
        if deleted:
            return {
                "status": "ok",
                "reply_to_user_hebrew": f"מחקתי את זיכרון #{memory_id}.",
            }
        return {"error": f"Memory #{memory_id} not found"}
