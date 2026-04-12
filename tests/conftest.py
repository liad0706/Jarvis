"""Shared fixtures for Jarvis tests."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.event_bus import EventBus
from core.memory import Memory
from core.skill_base import BaseSkill, SkillRegistry


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
async def memory(tmp_path):
    """In-memory/temp-path memory for tests."""
    db_path = tmp_path / "test_memory.db"
    mem = Memory(db_path=db_path)
    await mem.init()
    yield mem
    await mem.close()


@pytest.fixture
def registry():
    return SkillRegistry()


class DummySkill(BaseSkill):
    """A simple test skill."""

    name = "dummy"
    description = "A dummy skill for testing"

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        return await method(**(params or {}))

    async def do_greet(self, name: str = "World") -> dict:
        """Say hello to someone."""
        return {"message": f"Hello, {name}!"}

    async def do_add(self, a: int, b: int) -> dict:
        """Add two numbers."""
        return {"result": int(a) + int(b)}


@pytest.fixture
def dummy_skill():
    return DummySkill()
