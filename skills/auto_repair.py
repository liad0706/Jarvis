"""Auto-repair skill — diagnose, fix, validate, and restart Jarvis autonomously."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from core.skill_base import BaseSkill, SkillRegistry

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_CHECK_COMMANDS: dict[str, list[str]] = {
    "pytest": [sys.executable, "-m", "pytest", "--tb=short", "-q"],
    "ruff": [sys.executable, "-m", "ruff", "check", "."],
    "mypy": [sys.executable, "-m", "mypy", "--ignore-missing-imports", "."],
}


class AutoRepairSkill(BaseSkill):
    name = "auto_repair"
    description = (
        "Autonomous repair pipeline for Jarvis: analyze suspicious code, "
        "apply edits, run checks (pytest/ruff/mypy), create missing skills, "
        "and restart the process — all without leaving the conversation."
    )

    RISK_MAP = {
        "analyze": "low",
        "apply_edit": "medium",
        "run_checks": "low",
        "create_missing_skill": "medium",
        "restart": "medium",
    }

    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("auto_repair.%s failed", action)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # 1. analyze — find suspicious files / search for relevant code
    # ------------------------------------------------------------------

    async def do_analyze(self, query: str, path: str = "") -> dict:
        """Search the codebase for code related to *query* (error message, function name, etc.).
        Optionally narrow to a sub-path like 'core' or 'skills'."""
        introspect = self.registry.get("introspect")
        if introspect is None:
            return {"error": "IntrospectSkill not registered — cannot analyze."}

        search_result = await introspect.execute("search", {"pattern": query, "path": path})
        if search_result.get("status") == "empty":
            return {
                "status": "no_matches",
                "query": query,
                "reply_to_user_hebrew": f"לא מצאתי התאמות ל-'{query}' בקוד.",
            }
        if "error" in search_result:
            return search_result

        tree_result = await introspect.execute("tree", {"path": path or "", "depth": 2})

        return {
            "status": "ok",
            "query": query,
            "matches": search_result.get("matches", ""),
            "match_count": search_result.get("count", 0),
            "tree": tree_result.get("tree", ""),
            "reply_to_user_hebrew": f"מצאתי {search_result.get('count', 0)} התאמות. מוכן לתקן.",
        }

    # ------------------------------------------------------------------
    # 2. apply_edit — delegate to self_improve.edit_file
    # ------------------------------------------------------------------

    async def do_apply_edit(self, file_path: str, old_text: str, new_text: str) -> dict:
        """Apply a surgical text replacement in a project file.
        Delegates to self_improve.edit_file with full validation."""
        self_improve = self.registry.get("self_improve")
        if self_improve is None:
            return {"error": "SelfImproveSkill not registered — cannot edit."}

        result = await self_improve.execute(
            "edit_file",
            {"file_path": file_path, "old_text": old_text, "new_text": new_text},
        )
        return result

    # ------------------------------------------------------------------
    # 3. run_checks — pytest / ruff / mypy
    # ------------------------------------------------------------------

    async def do_run_checks(self, tools: str = "pytest") -> dict:
        """Run one or more code-quality checks. tools is a comma-separated list
        of: pytest, ruff, mypy  (default: pytest)."""
        requested = [t.strip().lower() for t in tools.split(",") if t.strip()]
        if not requested:
            requested = ["pytest"]

        unknown = [t for t in requested if t not in _CHECK_COMMANDS]
        if unknown:
            return {"error": f"Unknown check tool(s): {', '.join(unknown)}. Use: pytest, ruff, mypy."}

        results: dict[str, dict] = {}
        for tool_name in requested:
            cmd = _CHECK_COMMANDS[tool_name]
            try:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(PROJECT_ROOT),
                )
                results[tool_name] = {
                    "passed": proc.returncode == 0,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-3000:] if proc.stdout else "",
                    "stderr": proc.stderr[-1500:] if proc.stderr else "",
                }
            except subprocess.TimeoutExpired:
                results[tool_name] = {"passed": False, "error": "Timed out after 120s"}
            except FileNotFoundError:
                results[tool_name] = {"passed": False, "error": f"{tool_name} not installed"}

        all_passed = all(r.get("passed", False) for r in results.values())
        return {
            "status": "ok" if all_passed else "failures",
            "all_passed": all_passed,
            "results": results,
            "reply_to_user_hebrew": "כל הבדיקות עברו!" if all_passed else "יש כשלים — ראה פירוט.",
        }

    # ------------------------------------------------------------------
    # 4. create_missing_skill — delegate to self_improve.create
    # ------------------------------------------------------------------

    async def do_create_missing_skill(self, capability_description: str, skill_name: str = "") -> dict:
        """Generate and hot-load a brand-new skill via self_improve.create."""
        self_improve = self.registry.get("self_improve")
        if self_improve is None:
            return {"error": "SelfImproveSkill not registered — cannot create skills."}

        result = await self_improve.execute(
            "create",
            {"capability_description": capability_description, "skill_name": skill_name},
        )
        return result

    # ------------------------------------------------------------------
    # 5. restart — save context and restart the process
    # ------------------------------------------------------------------

    async def do_restart(self, reason: str = "auto_repair applied changes") -> dict:
        """Restart the Jarvis process so code changes take effect.
        Saves restart context so Jarvis can resume the conversation."""
        restart_skill = self.registry.get("restart")
        if restart_skill is None:
            return {"error": "RestartSkill not registered — cannot restart."}

        result = await restart_skill.execute(
            "restart",
            {"reason": reason, "resume_message": "Jarvis restarted after auto-repair."},
        )
        return result
