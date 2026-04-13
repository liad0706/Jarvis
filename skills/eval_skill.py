"""Evaluation skill — lets the LLM run evaluation suites on itself."""

from __future__ import annotations

from core.skill_base import BaseSkill


class EvalSkill(BaseSkill):
    name = "eval"
    description = "Run evaluation benchmarks to test Jarvis skill quality and performance"
    RISK_MAP = {"run_suite": "READ", "list_suites": "READ"}

    def __init__(self, evaluator):
        self._evaluator = evaluator

    async def execute(self, action: str, params: dict | None = None) -> dict:
        params = params or {}
        method = getattr(self, f"do_{action}", None)
        if method is None:
            return {"error": f"Unknown eval action: {action}"}
        return await method(**params)

    async def do_list_suites(self) -> dict:
        """List available evaluation suites."""
        from core.evaluation import SUITES
        return {
            "status": "ok",
            "suites": {
                name: len(tests) for name, tests in SUITES.items()
            },
        }

    async def do_run_suite(self, suite_name: str = "basic") -> dict:
        """Run an evaluation suite and return results."""
        result = await self._evaluator.run_suite(suite_name)
        return {
            "status": "ok",
            "summary": self._evaluator.format_results(result),
            "passed": result.passed,
            "failed": result.failed,
            "avg_score": round(result.avg_score, 2),
        }
