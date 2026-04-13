"""Telemetry skill — lets the LLM query Jarvis usage stats."""

from __future__ import annotations

from core.skill_base import BaseSkill


class TelemetrySkill(BaseSkill):
    name = "telemetry"
    description = "Query Jarvis usage statistics — tokens, costs, latency, call counts"

    def __init__(self, telemetry_collector):
        self._telemetry = telemetry_collector

    async def execute(self, action: str, params: dict | None = None) -> dict:
        params = params or {}
        method = getattr(self, f"do_{action}", None)
        if method is None:
            return {"error": f"Unknown telemetry action: {action}"}
        return await method(**params)

    async def do_summary(self, hours: int = 24) -> dict:
        """Get a summary of Jarvis usage stats for the last N hours."""
        stats = await self._telemetry.get_stats(hours=hours)
        return {
            "status": "ok",
            "summary": await self._telemetry.get_today_summary(),
            "total_calls": stats.total_calls,
            "total_cost_usd": round(stats.total_cost_usd, 4),
            "avg_latency_ms": round(stats.avg_latency_ms, 0),
            "error_rate": round(stats.error_rate, 3),
        }
