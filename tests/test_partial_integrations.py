from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.activity_manager import ActivityManager
from core.embeddings import EmbeddingEngine
from core.memory import Memory
from core.memory_manager import MemoryManager
from core.orchestrator import Orchestrator
from core.skill_base import BaseSkill, SkillRegistry
from core.verification import VerificationResult, verify_skill_file
from skills.memory_skill import MemorySkill
from skills.self_improve import SelfImproveSkill


VALID_DYNAMIC_SKILL = """
import logging

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)


class EchoerSkill(BaseSkill):
    name = "echoer"
    description = "Echoes text back"

    def __init__(self):
        self.prefix = "echo"

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as exc:
            return {"error": str(exc)}

    async def do_echo(self, text: str = "") -> dict:
        cleaned = (text or "").strip()
        message = cleaned or "empty"
        logger.info("echo %s", message)
        return {"status": "ok", "message": f"{self.prefix}:{message}"}
""".strip()


BROKEN_DYNAMIC_SKILL = """
from core.skill_base import BaseSkill


class BrokenSkill(BaseSkill):
    name = "broken"
    description = "Breaks the unknown action contract"

    def __init__(self):
        self.prefix = "broken"

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"status": "ok"}
        try:
            return await method(**(params or {}))
        except Exception as exc:
            return {"error": str(exc)}

    async def do_ping(self, text: str = "") -> dict:
        payload = text or "ping"
        return {"status": "ok", "message": f"{self.prefix}:{payload}"}
""".strip()


class AlphaSkill(BaseSkill):
    name = "alpha"
    description = "First test skill"

    async def execute(self, action: str, params: dict | None = None) -> dict:
        return {"status": "ok"}

    async def do_ping(self) -> dict:
        return {"status": "ok"}


class BetaSkill(BaseSkill):
    name = "beta"
    description = "Second test skill"

    async def execute(self, action: str, params: dict | None = None) -> dict:
        return {"status": "ok"}

    async def do_pong(self) -> dict:
        return {"status": "ok"}


@pytest.fixture
async def scoped_memory_manager(tmp_path, monkeypatch):
    user_dir = tmp_path / "user_memory"
    project_dir = tmp_path / "project_memory"
    local_dir = tmp_path / "local_memory"

    def _ensure(path):
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr("core.memory_scopes.get_user_memory_dir", lambda: _ensure(user_dir))
    monkeypatch.setattr("core.memory_scopes.get_project_memory_dir", lambda: _ensure(project_dir))
    monkeypatch.setattr("core.memory_scopes.get_local_memory_dir", lambda: _ensure(local_dir))

    mem = Memory(db_path=tmp_path / "scoped_memory.db")
    await mem.init()
    embeddings = EmbeddingEngine(db=mem._db)
    embeddings._disabled = True

    with patch("core.memory_manager.get_settings") as mock_settings:
        mock_settings.return_value = SimpleNamespace(
            ollama_host="http://localhost:11434",
            ollama_model="test",
            embedding_model="nomic-embed-text",
            summarize_threshold=40,
        )
        manager = MemoryManager(memory=mem, embeddings=embeddings)
        yield manager

    await mem.close()


def test_activity_manager_deduplicates_repeated_events():
    manager = ActivityManager(dedup_window_seconds=60.0)

    first = manager.record("tool", "dummy_ping", detail="ok", status="ok")
    second = manager.record("tool", "dummy_ping", detail="ok", status="ok")

    assert manager.count == 1
    assert first["count"] == 1
    assert second["count"] == 2


@pytest.mark.asyncio
async def test_orchestrator_invalidates_cached_skills_summary(memory):
    registry = SkillRegistry()
    registry.register(AlphaSkill())
    orchestrator = Orchestrator(registry, memory)

    first = orchestrator._get_skills_summary()
    second = orchestrator._get_skills_summary()
    registry.register(BetaSkill())
    third = orchestrator._get_skills_summary()

    assert first == second
    assert "**alpha**" in first
    assert "**beta**" in third
    assert third != first


@pytest.mark.asyncio
async def test_memory_skill_supports_scoped_memory_notes(scoped_memory_manager):
    skill = MemorySkill(scoped_memory_manager)

    user_res = await skill.execute("remember", {"content": "global preference", "scope": "user"})
    project_res = await skill.execute("remember", {"content": "project fact", "scope": "project"})
    local_res = await skill.execute("remember", {"content": "scratch pad", "scope": "local"})

    assert user_res["scope"] == "user"
    assert project_res["scope"] == "project"
    assert local_res["scope"] == "local"

    listed = await skill.execute("list_scoped", {"scope": "local"})
    assert listed["status"] == "ok"
    assert listed["count"] == 1

    read_back = await skill.execute("read_scoped", {"key": listed["keys"][0], "scope": "local"})
    assert read_back["status"] == "ok"
    assert "scratch pad" in read_back["content"]


@pytest.mark.asyncio
async def test_verification_passes_for_valid_skill(tmp_path):
    skill_path = tmp_path / "echoer.py"
    skill_path.write_text(VALID_DYNAMIC_SKILL, encoding="utf-8")

    result = await verify_skill_file(skill_path)

    assert result.passed is True
    assert result.verdict == "PASS"
    assert all(check["ok"] for check in result.checks)


@pytest.mark.asyncio
async def test_verification_fails_for_broken_unknown_action_contract(tmp_path):
    skill_path = tmp_path / "broken.py"
    skill_path.write_text(BROKEN_DYNAMIC_SKILL, encoding="utf-8")

    result = await verify_skill_file(skill_path)

    assert result.passed is False
    assert any(check["name"] == "unknown_action_contract" and not check["ok"] for check in result.checks)


@pytest.mark.asyncio
async def test_self_improve_create_rejects_failed_verification(tmp_path, monkeypatch):
    monkeypatch.setattr("skills.self_improve.DYNAMIC_SKILLS_DIR", tmp_path)
    monkeypatch.setattr("skills.self_improve.MANIFEST_PATH", tmp_path / "manifest.json")

    registry = SkillRegistry()
    skill = SelfImproveSkill(registry)
    skill._SelfImproveSkill__provider = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(content=VALID_DYNAMIC_SKILL)),
        name="fake-provider",
    )

    failing_verification = VerificationResult(
        passed=False,
        checks=[{"name": "runtime", "ok": False, "detail": "boom"}],
        verdict="FAIL",
    )
    monkeypatch.setattr(
        "skills.self_improve.verify_skill_file",
        AsyncMock(return_value=failing_verification),
    )

    result = await skill.do_create("Create a tiny echo skill", "echoer")

    assert "failed verification" in result["error"].lower()
    assert not (tmp_path / "echoer.py").exists()
