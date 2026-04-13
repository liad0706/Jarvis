"""Tests for AutoRepairSkill."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.skill_base import SkillRegistry
from skills.auto_repair import AutoRepairSkill


@pytest.fixture
def registry():
    return SkillRegistry()


@pytest.fixture
def skill(registry):
    s = AutoRepairSkill(registry)
    registry.register(s)
    return s


# ── analyze ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analyze_delegates_to_introspect(skill, registry):
    mock_introspect = AsyncMock()
    mock_introspect.name = "introspect"
    mock_introspect.execute = AsyncMock(side_effect=[
        {"status": "ok", "count": 2, "matches": "core/foo.py:1: hit\ncore/bar.py:3: hit"},
        {"status": "ok", "tree": "core/\n  foo.py"},
    ])
    registry.register(mock_introspect)

    result = await skill.execute("analyze", {"query": "some_error"})
    assert result["status"] == "ok"
    assert result["match_count"] == 2


@pytest.mark.asyncio
async def test_analyze_no_introspect(skill):
    result = await skill.execute("analyze", {"query": "x"})
    assert "error" in result


@pytest.mark.asyncio
async def test_analyze_no_matches(skill, registry):
    mock_introspect = AsyncMock()
    mock_introspect.name = "introspect"
    mock_introspect.execute = AsyncMock(return_value={"status": "empty"})
    registry.register(mock_introspect)

    result = await skill.execute("analyze", {"query": "nonexistent"})
    assert result["status"] == "no_matches"


# ── apply_edit ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_edit_delegates_to_self_improve(skill, registry):
    mock_si = AsyncMock()
    mock_si.name = "self_improve"
    mock_si.execute = AsyncMock(return_value={"status": "ok", "file": "test.py", "replacements": 1, "total_matches": 1})
    registry.register(mock_si)

    result = await skill.execute("apply_edit", {"file_path": "test.py", "old_text": "a", "new_text": "b"})
    assert result["status"] == "ok"
    mock_si.execute.assert_called_once_with("edit_file", {"file_path": "test.py", "old_text": "a", "new_text": "b"})


@pytest.mark.asyncio
async def test_apply_edit_no_self_improve(skill):
    result = await skill.execute("apply_edit", {"file_path": "x", "old_text": "a", "new_text": "b"})
    assert "error" in result


# ── run_checks ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_checks_default_pytest(skill, monkeypatch):
    import subprocess
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "5 passed"
    fake_result.stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

    result = await skill.execute("run_checks", {})
    assert result["all_passed"] is True
    assert "pytest" in result["results"]


@pytest.mark.asyncio
async def test_run_checks_unknown_tool(skill):
    result = await skill.execute("run_checks", {"tools": "banana"})
    assert "error" in result


# ── create_missing_skill ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_missing_skill_delegates(skill, registry):
    mock_si = AsyncMock()
    mock_si.name = "self_improve"
    mock_si.execute = AsyncMock(return_value={"status": "created", "skill_name": "foo"})
    registry.register(mock_si)

    result = await skill.execute("create_missing_skill", {"capability_description": "do foo", "skill_name": "foo"})
    assert result["status"] == "created"


# ── restart ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restart_delegates(skill, registry):
    mock_restart = AsyncMock()
    mock_restart.name = "restart"
    mock_restart.execute = AsyncMock(return_value={"status": "restarting"})
    registry.register(mock_restart)

    result = await skill.execute("restart", {"reason": "test"})
    assert result["status"] == "restarting"


@pytest.mark.asyncio
async def test_restart_no_skill(skill):
    result = await skill.execute("restart", {})
    assert "error" in result


# ── unknown action ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_action(skill):
    result = await skill.execute("bogus", {})
    assert "error" in result
