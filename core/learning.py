"""Learning system — learns from user feedback to improve over time.

Implements Reinforcement Learning from Meaningful feedback (RLM):
1. Track which responses/actions got positive vs negative feedback
2. Build preference profiles per skill and action
3. Adjust system prompt and tool selection based on learned patterns
4. Store feedback in SQLite for persistence across sessions

This is NOT neural-network RL — it's structured learning via counting,
scoring, and preference accumulation. Works offline, no GPU needed.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "learning.db"


@dataclass
class FeedbackEntry:
    """One piece of user feedback on a Jarvis action."""
    id: int = 0
    timestamp: float = 0.0
    query: str = ""
    skill_name: str = ""
    action: str = ""
    response_summary: str = ""
    rating: int = 0  # -1 = negative, 0 = neutral, 1 = positive
    feedback_text: str = ""
    context: dict = field(default_factory=dict)


@dataclass
class SkillPreference:
    """Learned preference for a skill/action."""
    skill_name: str
    action: str
    total_uses: int = 0
    positive: int = 0
    negative: int = 0
    neutral: int = 0
    avg_rating: float = 0.0
    learned_notes: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total_uses == 0:
            return 0.0
        return self.positive / self.total_uses


@dataclass
class QueryPattern:
    """Learned pattern: certain query types → preferred skills/approaches."""
    pattern: str
    preferred_skill: str
    preferred_action: str
    confidence: float = 0.0
    examples: list[str] = field(default_factory=list)


class LearningEngine:
    """Tracks feedback, builds preferences, and provides learning insights."""

    def __init__(self, db_path: Path | str = DB_PATH):
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        # In-memory caches
        self._skill_prefs: dict[str, SkillPreference] = {}
        self._query_patterns: list[QueryPattern] = []
        self._feedback_buffer: list[FeedbackEntry] = []

    async def init(self):
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                query TEXT DEFAULT '',
                skill_name TEXT DEFAULT '',
                action TEXT DEFAULT '',
                response_summary TEXT DEFAULT '',
                rating INTEGER DEFAULT 0,
                feedback_text TEXT DEFAULT '',
                context TEXT DEFAULT '{}'
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS learned_preferences (
                skill_action TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at REAL
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS query_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT NOT NULL,
                preferred_skill TEXT NOT NULL,
                preferred_action TEXT DEFAULT '',
                confidence REAL DEFAULT 0,
                examples TEXT DEFAULT '[]'
            )
        """)
        await self._db.commit()
        await self._load_preferences()
        await self._load_patterns()
        logger.info(
            "Learning: initialized (%d preferences, %d patterns)",
            len(self._skill_prefs), len(self._query_patterns),
        )

    async def _load_preferences(self):
        if not self._db:
            return
        cursor = await self._db.execute("SELECT skill_action, data FROM learned_preferences")
        for row in await cursor.fetchall():
            try:
                data = json.loads(row[1])
                pref = SkillPreference(**data)
                key = f"{pref.skill_name}:{pref.action}"
                self._skill_prefs[key] = pref
            except Exception:
                pass

    async def _load_patterns(self):
        if not self._db:
            return
        cursor = await self._db.execute(
            "SELECT pattern, preferred_skill, preferred_action, confidence, examples FROM query_patterns"
        )
        self._query_patterns = []
        for row in await cursor.fetchall():
            try:
                self._query_patterns.append(QueryPattern(
                    pattern=row[0],
                    preferred_skill=row[1],
                    preferred_action=row[2],
                    confidence=row[3],
                    examples=json.loads(row[4]) if row[4] else [],
                ))
            except Exception:
                pass

    async def record_feedback(
        self,
        query: str,
        skill_name: str,
        action: str,
        rating: int,
        response_summary: str = "",
        feedback_text: str = "",
        context: dict | None = None,
    ) -> FeedbackEntry:
        """Record user feedback on a Jarvis action.

        rating: -1 (bad), 0 (neutral), 1 (good)
        """
        entry = FeedbackEntry(
            timestamp=time.time(),
            query=query,
            skill_name=skill_name,
            action=action,
            response_summary=response_summary[:500],
            rating=max(-1, min(1, rating)),
            feedback_text=feedback_text[:500],
            context=context or {},
        )

        if self._db:
            await self._db.execute(
                """INSERT INTO feedback (timestamp, query, skill_name, action,
                   response_summary, rating, feedback_text, context)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry.timestamp, entry.query, entry.skill_name, entry.action,
                 entry.response_summary, entry.rating, entry.feedback_text,
                 json.dumps(entry.context, ensure_ascii=False)),
            )
            await self._db.commit()

        # Update preferences
        await self._update_preference(skill_name, action, rating, feedback_text)

        logger.info(
            "Learning: feedback recorded — %s.%s rating=%d",
            skill_name, action, rating,
        )
        return entry

    async def _update_preference(self, skill_name: str, action: str, rating: int, note: str = ""):
        key = f"{skill_name}:{action}"
        pref = self._skill_prefs.get(key) or SkillPreference(
            skill_name=skill_name, action=action,
        )
        pref.total_uses += 1
        if rating > 0:
            pref.positive += 1
        elif rating < 0:
            pref.negative += 1
        else:
            pref.neutral += 1

        total_rated = pref.positive + pref.negative
        pref.avg_rating = (pref.positive - pref.negative) / max(1, total_rated)

        if note and len(pref.learned_notes) < 20:
            pref.learned_notes.append(note[:200])

        self._skill_prefs[key] = pref

        # Persist
        if self._db:
            import dataclasses
            data = json.dumps(dataclasses.asdict(pref), ensure_ascii=False)
            await self._db.execute(
                """INSERT OR REPLACE INTO learned_preferences (skill_action, data, updated_at)
                   VALUES (?, ?, ?)""",
                (key, data, time.time()),
            )
            await self._db.commit()

    def get_skill_preference(self, skill_name: str, action: str = "") -> SkillPreference | None:
        if action:
            return self._skill_prefs.get(f"{skill_name}:{action}")
        # Return aggregate across all actions
        prefs = [v for k, v in self._skill_prefs.items() if k.startswith(f"{skill_name}:")]
        if not prefs:
            return None
        agg = SkillPreference(skill_name=skill_name, action="*")
        for p in prefs:
            agg.total_uses += p.total_uses
            agg.positive += p.positive
            agg.negative += p.negative
            agg.neutral += p.neutral
        agg.avg_rating = (agg.positive - agg.negative) / max(1, agg.positive + agg.negative)
        return agg

    def get_all_preferences(self) -> list[SkillPreference]:
        return sorted(
            self._skill_prefs.values(),
            key=lambda p: p.total_uses,
            reverse=True,
        )

    def get_learning_summary(self) -> str:
        """Human-readable summary of what Jarvis has learned."""
        prefs = self.get_all_preferences()
        if not prefs:
            return "עוד לא צברתי מספיק פידבק ללמידה."

        lines = ["📚 סיכום למידה:"]
        for p in prefs[:10]:
            emoji = "✅" if p.success_rate > 0.7 else "⚠️" if p.success_rate > 0.4 else "❌"
            lines.append(
                f"  {emoji} {p.skill_name}.{p.action}: "
                f"{p.total_uses} uses, {p.success_rate:.0%} success"
            )
            if p.learned_notes:
                lines.append(f"      Notes: {p.learned_notes[-1]}")
        return "\n".join(lines)

    def build_learning_context(self) -> str:
        """Build a context string for the system prompt with learned preferences."""
        prefs = self.get_all_preferences()
        if not prefs:
            return ""
        lines = ["Learned preferences from past interactions:"]
        for p in prefs[:8]:
            if p.total_uses >= 3:
                status = "preferred" if p.success_rate > 0.6 else "needs improvement"
                lines.append(f"  - {p.skill_name}.{p.action}: {status} ({p.total_uses} uses)")
        return "\n".join(lines) if len(lines) > 1 else ""

    async def close(self):
        if self._db:
            await self._db.close()
