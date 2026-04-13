"""Verification checks for newly created skills.

Used by self_improve to run an isolated adversarial-style pass before a newly
generated skill is registered for normal use.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    passed: bool
    checks: list[dict]
    verdict: str  # "PASS", "FAIL", "PARTIAL"

    @property
    def summary(self) -> str:
        lines = [f"VERDICT: {self.verdict}"]
        for check in self.checks:
            status = "PASS" if check["ok"] else "FAIL"
            detail = check.get("detail", "")
            lines.append(f"  [{status}] {check['name']}: {detail}".rstrip())
        return "\n".join(lines)


def _fail(checks: list[dict], verdict: str = "FAIL") -> VerificationResult:
    return VerificationResult(passed=False, checks=checks, verdict=verdict)


async def verify_skill_file(skill_path: str | Path) -> VerificationResult:
    """Run verification checks on a generated skill file."""
    skill_path = Path(skill_path)
    checks: list[dict] = []

    if not skill_path.exists():
        checks.append({"name": "file_exists", "ok": False, "detail": f"{skill_path} not found"})
        return _fail(checks)
    checks.append({"name": "file_exists", "ok": True, "detail": str(skill_path)})

    source = skill_path.read_text(encoding="utf-8")
    try:
        compile(source, str(skill_path), "exec")
        checks.append({"name": "syntax_valid", "ok": True})
    except SyntaxError as exc:
        checks.append({"name": "syntax_valid", "ok": False, "detail": str(exc)})
        return _fail(checks)

    module_name = f"_verify_{skill_path.stem}"
    module = None
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(skill_path))
        if spec is None or spec.loader is None:
            checks.append({"name": "import", "ok": False, "detail": "Could not create module spec"})
            return _fail(checks)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        checks.append({"name": "import", "ok": True})
    except Exception as exc:
        checks.append({"name": "import", "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
        return _fail(checks)
    finally:
        sys.modules.pop(module_name, None)

    from core.skill_base import BaseSkill

    skill_cls = None
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, BaseSkill)
            and attr is not BaseSkill
            and not attr.__name__.startswith("_")
        ):
            skill_cls = attr
            break

    if skill_cls is None:
        checks.append({"name": "skill_class", "ok": False, "detail": "No BaseSkill subclass found"})
        return _fail(checks)
    checks.append({"name": "skill_class", "ok": True, "detail": skill_cls.__name__})

    try:
        instance = skill_cls()
    except Exception as exc:
        checks.append({"name": "instantiate", "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
        return _fail(checks)
    checks.append({"name": "instantiate", "ok": True})

    try:
        actions = instance.get_actions()
        if not actions:
            checks.append({"name": "has_actions", "ok": False, "detail": "No do_* methods found"})
            return _fail(checks)
        checks.append({"name": "has_actions", "ok": True, "detail": ", ".join(actions)})
    except Exception as exc:
        checks.append({"name": "has_actions", "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
        return _fail(checks)

    try:
        tools = instance.as_tools()
        if not tools:
            checks.append({"name": "tool_defs", "ok": False, "detail": "as_tools() returned empty list"})
        else:
            checks.append({"name": "tool_defs", "ok": True, "detail": f"{len(tools)} tool definitions"})
    except Exception as exc:
        checks.append({"name": "tool_defs", "ok": False, "detail": f"{type(exc).__name__}: {exc}"})

    try:
        unknown_result = await asyncio.wait_for(
            instance.execute("__nonexistent_verification_action__", {}),
            timeout=10.0,
        )
        ok = isinstance(unknown_result, dict) and "error" in unknown_result
        detail = ""
        if not isinstance(unknown_result, dict):
            detail = f"execute() returned {type(unknown_result).__name__}, expected dict"
        elif "error" not in unknown_result:
            detail = "Unknown action did not return {'error': ...}"
        checks.append({"name": "unknown_action_contract", "ok": ok, "detail": detail})
    except Exception as exc:
        checks.append({"name": "unknown_action_contract", "ok": False, "detail": f"{type(exc).__name__}: {exc}"})

    first_action = actions[0]
    try:
        first_result = await asyncio.wait_for(instance.execute(first_action, {}), timeout=10.0)
        ok = isinstance(first_result, dict)
        detail = ""
        if not isinstance(first_result, dict):
            detail = f"{first_action} returned {type(first_result).__name__}, expected dict"
        elif "error" in first_result:
            detail = str(first_result["error"])[:120]
        else:
            detail = "returned dict successfully"
        checks.append({"name": "first_action_smoke", "ok": ok, "detail": detail})
    except Exception as exc:
        checks.append({"name": "first_action_smoke", "ok": False, "detail": f"{type(exc).__name__}: {exc}"})

    passed = all(check["ok"] for check in checks)
    return VerificationResult(
        passed=passed,
        checks=checks,
        verdict="PASS" if passed else "PARTIAL",
    )


async def verify_and_report(skill_path: str | Path) -> str:
    result = await verify_skill_file(skill_path)
    return result.summary
