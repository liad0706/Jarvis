"""Learning skill — lets the LLM report/query learned preferences."""

from __future__ import annotations

from core.skill_base import BaseSkill


class LearningSkill(BaseSkill):
    name = "learning"
    description = "Report user feedback and query what Jarvis has learned from past interactions"

    def __init__(self, learning_engine):
        self._learning = learning_engine

    async def execute(self, action: str, params: dict | None = None) -> dict:
        params = params or {}
        method = getattr(self, f"do_{action}", None)
        if method is None:
            return {"error": f"Unknown learning action: {action}"}
        return await method(**params)

    async def do_feedback(self, query: str = "", skill_name: str = "", action: str = "", rating: int = 0, note: str = "") -> dict:
        """Record user feedback on a previous action. rating: -1=bad, 0=neutral, 1=good."""
        entry = await self._learning.record_feedback(
            query=query,
            skill_name=skill_name,
            action=action,
            rating=rating,
            feedback_text=note,
        )
        return {"status": "ok", "recorded": True}

    async def do_summary(self) -> dict:
        """Get a summary of what Jarvis has learned."""
        return {
            "status": "ok",
            "summary": self._learning.get_learning_summary(),
            "preferences": [
                {
                    "skill": p.skill_name,
                    "action": p.action,
                    "uses": p.total_uses,
                    "success_rate": round(p.success_rate, 2),
                }
                for p in self._learning.get_all_preferences()[:10]
            ],
        }
