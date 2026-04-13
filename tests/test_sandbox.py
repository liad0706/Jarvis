"""Tests for the sandbox executor."""

import asyncio
from pathlib import Path

import pytest

from core.sandbox import SandboxExecutor


@pytest.fixture
def sandbox(tmp_path):
    project_root = Path(__file__).resolve().parent.parent
    return SandboxExecutor(timeout=10, project_root=project_root)


@pytest.fixture
def valid_skill_file(tmp_path):
    code = '''
from core.skill_base import BaseSkill

class TestSandboxSkill(BaseSkill):
    name = "test_sandbox"
    description = "A test skill for sandbox"

    async def execute(self, action, params=None):
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        return await method(**(params or {}))

    async def do_echo(self, text: str = "hello") -> dict:
        """Echo text back."""
        return {"status": "ok", "text": text}
'''
    skill_path = tmp_path / "test_skill.py"
    skill_path.write_text(code, encoding="utf-8")
    return skill_path


@pytest.mark.asyncio
class TestSandboxExecutor:
    async def test_execute_valid_skill(self, sandbox, valid_skill_file):
        result = await sandbox.execute(valid_skill_file, "echo", {"text": "world"})
        assert result.get("status") == "ok"
        assert result.get("text") == "world"

    async def test_execute_unknown_action(self, sandbox, valid_skill_file):
        result = await sandbox.execute(valid_skill_file, "nonexistent", {})
        assert "error" in result

    async def test_execute_nonexistent_file(self, sandbox, tmp_path):
        result = await sandbox.execute(tmp_path / "nope.py", "test", {})
        assert "error" in result

    async def test_timeout(self, tmp_path):
        code = '''
import time
from core.skill_base import BaseSkill

class SlowSkill(BaseSkill):
    name = "slow"
    description = "Hangs forever"

    async def execute(self, action, params=None):
        import asyncio
        await asyncio.sleep(100)
        return {"status": "ok"}

    async def do_hang(self):
        import asyncio
        await asyncio.sleep(100)
        return {}
'''
        skill_path = tmp_path / "slow_skill.py"
        skill_path.write_text(code, encoding="utf-8")

        project_root = Path(__file__).resolve().parent.parent
        sb = SandboxExecutor(timeout=2, project_root=project_root)
        result = await sb.execute(skill_path, "hang", {})
        assert "error" in result
        assert "timed out" in result["error"].lower()
