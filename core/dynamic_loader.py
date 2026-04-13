"""Dynamic skill loader — imports BaseSkill subclasses from skills/dynamic/ at runtime."""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path

from core.skill_base import BaseSkill, SkillRegistry

logger = logging.getLogger(__name__)

DYNAMIC_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills" / "dynamic"


def import_skill_module(path: Path) -> BaseSkill | None:
    """Import a single .py file and return an instance of the first BaseSkill subclass found."""
    module_name = f"skills.dynamic.{path.stem}"

    try:
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None or spec.loader is None:
            logger.warning("Cannot create module spec for %s", path)
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseSkill) and obj is not BaseSkill:
                instance = obj()
                return instance

    except Exception as e:
        logger.exception("Failed to import dynamic skill from %s: %s", path, e)

    return None


class SandboxedSkillProxy(BaseSkill):
    """Wraps a dynamic skill so its execute() runs in a subprocess sandbox."""

    def __init__(self, original: BaseSkill, skill_path: Path):
        self.name = original.name
        self.description = original.description
        self.RISK_MAP = getattr(original, "RISK_MAP", {})
        self._original = original
        self._skill_path = skill_path
        self._sandbox = None

    def _get_sandbox(self):
        if self._sandbox is None:
            from core.sandbox import SandboxExecutor
            self._sandbox = SandboxExecutor()
        return self._sandbox

    def get_actions(self) -> list[str]:
        return self._original.get_actions()

    def as_tools(self) -> list[dict]:
        return self._original.as_tools()

    async def execute(self, action: str, params: dict | None = None) -> dict:
        sandbox = self._get_sandbox()
        return await sandbox.execute(self._skill_path, action, params)


def load_dynamic_skills(registry: SkillRegistry, sandbox: bool = True) -> int:
    """Scan skills/dynamic/ and register every BaseSkill found. Returns count loaded."""
    DYNAMIC_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    loaded = 0

    for py_file in sorted(DYNAMIC_SKILLS_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        skill = import_skill_module(py_file)
        if skill is None:
            continue

        if registry.get(skill.name):
            logger.debug("Skill '%s' already registered, skipping", skill.name)
            continue

        if sandbox:
            skill = SandboxedSkillProxy(skill, py_file)
            logger.info("Loaded dynamic skill '%s' from %s (sandboxed)", skill.name, py_file.name)
        else:
            logger.info("Loaded dynamic skill '%s' from %s", skill.name, py_file.name)

        registry.register(skill)
        loaded += 1

    return loaded
