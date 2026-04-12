"""Evaluation system — benchmark Jarvis skills and agent performance.

Runs predefined test scenarios against skills/orchestrator and scores results.
Helps track quality over time and catch regressions when changing prompts or models.

Usage:
    evaluator = Evaluator(orchestrator)
    results = await evaluator.run_suite("basic")
    print(evaluator.format_results(results))
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

EVALS_DIR = Path(__file__).resolve().parent.parent / "data" / "evals"


@dataclass
class TestCase:
    """A single evaluation test case."""
    name: str
    query: str
    expected_skill: str = ""       # Expected skill to be used
    expected_action: str = ""      # Expected action to be called
    expected_contains: list[str] = field(default_factory=list)  # Response should contain
    expected_not_contains: list[str] = field(default_factory=list)
    max_latency_ms: float = 30000  # Max acceptable latency
    tags: list[str] = field(default_factory=list)


@dataclass
class TestResult:
    """Result of one test case execution."""
    test_name: str
    passed: bool
    score: float = 0.0         # 0.0 to 1.0
    response: str = ""
    skill_used: str = ""
    action_used: str = ""
    latency_ms: float = 0.0
    checks: dict[str, bool] = field(default_factory=dict)
    error: str = ""


@dataclass
class SuiteResult:
    """Results of a full test suite."""
    suite_name: str
    results: list[TestResult] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    avg_score: float = 0.0
    total_time_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


# ── Predefined test suites ──────────────────────────────────────────

BASIC_SUITE: list[TestCase] = [
    TestCase(
        name="greeting_hebrew",
        query="היי מה קורה",
        expected_contains=["שלום", "היי", "מה", "קורה"],
        tags=["personality", "hebrew"],
    ),
    TestCase(
        name="spotify_play",
        query="תנגן שיר של עדן חסון",
        expected_skill="spotify",
        expected_action="play",
        tags=["skill", "spotify"],
    ),
    TestCase(
        name="system_info",
        query="מה שעון עכשיו?",
        expected_skill="system",
        tags=["skill", "system"],
    ),
    TestCase(
        name="memory_recall",
        query="מה אתה זוכר על הפרויקטים שלי?",
        expected_skill="memory",
        tags=["skill", "memory"],
    ),
    TestCase(
        name="code_generation",
        query="תכתוב לי פונקציה ב-Python שמחשבת מספר ראשוני",
        expected_skill="code_writer",
        expected_contains=["def", "prime"],
        tags=["skill", "code"],
    ),
    TestCase(
        name="smart_home",
        query="תדליק את האור בחדר",
        expected_skill="smart_home",
        expected_action="turn_on",
        tags=["skill", "smarthome"],
    ),
    TestCase(
        name="web_search",
        query="תחפש מה זה FAISS",
        expected_skill="web_research",
        tags=["skill", "web"],
    ),
    TestCase(
        name="calendar_check",
        query="מה יש לי היום?",
        expected_skill="calendar",
        tags=["skill", "calendar"],
    ),
]


ADVANCED_SUITE: list[TestCase] = [
    TestCase(
        name="multi_step_task",
        query="תבדוק מזג אוויר בהעיר ותגיד לי אם כדאי לצאת היום",
        expected_skill="web_research",
        max_latency_ms=60000,
        tags=["multi_step"],
    ),
    TestCase(
        name="context_awareness",
        query="בהמשך למה שדיברנו קודם, תזכיר לי מה החלטנו",
        expected_skill="memory",
        tags=["context"],
    ),
    TestCase(
        name="hebrew_understanding",
        query="אני צריך עזרה עם הפרינטר התלת מימדי שלי, הוא לא מדפיס",
        expected_skill="creality_print",
        tags=["hebrew", "intent"],
    ),
]


SUITES: dict[str, list[TestCase]] = {
    "basic": BASIC_SUITE,
    "advanced": ADVANCED_SUITE,
}


# ── Evaluator ────────────────────────────────────────────────────────

class Evaluator:
    """Run evaluation suites against the orchestrator or skills."""

    def __init__(self, orchestrator=None, registry=None):
        self.orchestrator = orchestrator
        self.registry = registry
        self._results_dir = EVALS_DIR
        self._results_dir.mkdir(parents=True, exist_ok=True)

    def _score_result(self, test: TestCase, response: str, skill_used: str, action_used: str) -> tuple[float, dict[str, bool]]:
        """Score a test result. Returns (score, checks_dict)."""
        checks = {}
        points = 0
        total = 0

        # Check expected skill
        if test.expected_skill:
            total += 1
            match = test.expected_skill in skill_used
            checks["correct_skill"] = match
            if match:
                points += 1

        # Check expected action
        if test.expected_action:
            total += 1
            match = test.expected_action in action_used
            checks["correct_action"] = match
            if match:
                points += 1

        # Check expected content
        for keyword in test.expected_contains:
            total += 1
            found = keyword.lower() in response.lower()
            checks[f"contains_{keyword}"] = found
            if found:
                points += 1

        # Check not-contains
        for keyword in test.expected_not_contains:
            total += 1
            not_found = keyword.lower() not in response.lower()
            checks[f"not_contains_{keyword}"] = not_found
            if not_found:
                points += 1

        # Response not empty
        total += 1
        has_response = len(response.strip()) > 5
        checks["has_response"] = has_response
        if has_response:
            points += 1

        score = points / total if total > 0 else 1.0
        return score, checks

    async def run_test(self, test: TestCase) -> TestResult:
        """Run a single test case."""
        start = time.time()
        result = TestResult(test_name=test.name)

        try:
            if self.orchestrator:
                response = await self.orchestrator.process(test.query)
                result.response = response or ""
                # Try to detect which skill was used from orchestrator state
                result.skill_used = getattr(self.orchestrator, '_last_skill_used', '')
                result.action_used = getattr(self.orchestrator, '_last_action_used', '')
            else:
                result.response = "No orchestrator available"
                result.error = "No orchestrator"
        except Exception as e:
            result.error = str(e)
            result.response = ""

        result.latency_ms = (time.time() - start) * 1000

        # Score
        score, checks = self._score_result(
            test, result.response, result.skill_used, result.action_used,
        )
        result.score = score
        result.checks = checks

        # Latency check
        if result.latency_ms > test.max_latency_ms:
            checks["within_latency"] = False
        else:
            checks["within_latency"] = True

        result.passed = score >= 0.5 and not result.error
        return result

    async def run_suite(self, suite_name: str = "basic") -> SuiteResult:
        """Run a full test suite."""
        tests = SUITES.get(suite_name, BASIC_SUITE)
        suite = SuiteResult(suite_name=suite_name, total=len(tests))
        start = time.time()

        for test in tests:
            logger.info("Eval: running %s...", test.name)
            result = await self.run_test(test)
            suite.results.append(result)
            if result.passed:
                suite.passed += 1
            else:
                suite.failed += 1
            logger.info(
                "Eval: %s — %s (score=%.2f, %.0fms)",
                test.name, "PASS" if result.passed else "FAIL",
                result.score, result.latency_ms,
            )

        suite.total_time_ms = (time.time() - start) * 1000
        scores = [r.score for r in suite.results]
        suite.avg_score = sum(scores) / len(scores) if scores else 0

        # Save results
        self._save_results(suite)
        return suite

    def _save_results(self, suite: SuiteResult):
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = self._results_dir / f"eval_{suite.suite_name}_{ts}.json"
        data = {
            "suite": suite.suite_name,
            "timestamp": suite.timestamp,
            "total": suite.total,
            "passed": suite.passed,
            "failed": suite.failed,
            "avg_score": suite.avg_score,
            "total_time_ms": suite.total_time_ms,
            "results": [
                {
                    "name": r.test_name,
                    "passed": r.passed,
                    "score": r.score,
                    "latency_ms": r.latency_ms,
                    "checks": r.checks,
                    "error": r.error,
                    "response_preview": r.response[:200],
                }
                for r in suite.results
            ],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Eval: results saved to %s", path)

    def format_results(self, suite: SuiteResult) -> str:
        """Format results for display."""
        lines = [
            f"📋 Evaluation: {suite.suite_name}",
            f"   Total: {suite.total} | Passed: {suite.passed} | Failed: {suite.failed}",
            f"   Score: {suite.avg_score:.1%} | Time: {suite.total_time_ms:.0f}ms",
            "",
        ]
        for r in suite.results:
            emoji = "✅" if r.passed else "❌"
            lines.append(f"  {emoji} {r.test_name}: {r.score:.0%} ({r.latency_ms:.0f}ms)")
            if r.error:
                lines.append(f"      Error: {r.error}")
            for check, ok in r.checks.items():
                if not ok:
                    lines.append(f"      ✗ {check}")
        return "\n".join(lines)
