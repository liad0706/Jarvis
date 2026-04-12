"""Tests for the episodic memory system (Memory CRUD, MemoryManager sessions, MemorySkill)."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.memory import Memory
from core.memory_manager import MemoryManager
from core.embeddings import EmbeddingEngine
from skills.memory_skill import MemorySkill


# ── Fixtures ──

@pytest.fixture
async def memory(tmp_path):
    db_path = tmp_path / "episodic_test.db"
    mem = Memory(db_path=db_path)
    await mem.init()
    yield mem
    await mem.close()


@pytest.fixture
async def embedding_engine(memory):
    engine = EmbeddingEngine(db=memory._db)
    engine._disabled = True
    return engine


@pytest.fixture
async def memory_manager(memory, embedding_engine):
    with patch("core.memory_manager.get_settings") as mock_settings:
        s = MagicMock()
        s.ollama_host = "http://localhost:11434"
        s.ollama_model = "test"
        s.embedding_model = "nomic-embed-text"
        s.summarize_threshold = 40
        mock_settings.return_value = s
        mm = MemoryManager(memory=memory, embeddings=embedding_engine)
        yield mm


# ── Memory CRUD tests ──

@pytest.mark.asyncio
class TestEpisodicMemoryCRUD:
    async def test_add_episodic_memory(self, memory):
        mem_id = await memory.add_episodic_memory(
            "manual", "User likes local-first solutions",
            {"session_id": "abc123"},
        )
        assert mem_id is not None
        assert mem_id > 0

    async def test_get_episodic_memories(self, memory):
        await memory.add_episodic_memory("manual", "Memory 1")
        await memory.add_episodic_memory("session_summary", "Summary of session")
        await memory.add_episodic_memory("manual", "Memory 2")

        all_mems = await memory.get_episodic_memories()
        assert len(all_mems) == 3

        manual_only = await memory.get_episodic_memories(memory_type="manual")
        assert len(manual_only) == 2
        assert all(m["memory_type"] == "manual" for m in manual_only)

    async def test_get_episodic_memories_limit(self, memory):
        for i in range(10):
            await memory.add_episodic_memory("manual", f"Memory {i}")

        limited = await memory.get_episodic_memories(limit=3)
        assert len(limited) == 3

    async def test_get_episodic_memories_ordered_desc(self, memory):
        await memory.add_episodic_memory("manual", "First")
        await memory.add_episodic_memory("manual", "Second")

        mems = await memory.get_episodic_memories()
        assert mems[0]["content"] == "Second"
        assert mems[1]["content"] == "First"

    async def test_delete_episodic_memory(self, memory):
        mem_id = await memory.add_episodic_memory("manual", "To be deleted")
        deleted = await memory.delete_episodic_memory(mem_id)
        assert deleted is True

        remaining = await memory.get_episodic_memories()
        assert len(remaining) == 0

    async def test_delete_nonexistent(self, memory):
        deleted = await memory.delete_episodic_memory(99999)
        assert deleted is False

    async def test_update_recall_stats(self, memory):
        mem_id = await memory.add_episodic_memory("manual", "Recallable")
        await memory.update_recall_stats(mem_id)
        await memory.update_recall_stats(mem_id)

        mems = await memory.get_episodic_memories()
        assert len(mems) == 1
        assert mems[0]["recalled_count"] == 2
        assert mems[0]["last_recalled_at"] is not None

    async def test_metadata_stored_as_json(self, memory):
        meta = {"session_id": "s1", "topics": ["python", "AI"]}
        mem_id = await memory.add_episodic_memory("session_summary", "Test", meta)

        mems = await memory.get_episodic_memories()
        assert mems[0]["metadata"] == meta

    async def test_hebrew_content(self, memory):
        mem_id = await memory.add_episodic_memory(
            "manual", "המשתמש מעדיף פתרונות מקומיים על פני ענן"
        )
        mems = await memory.get_episodic_memories()
        assert mems[0]["content"] == "המשתמש מעדיף פתרונות מקומיים על פני ענן"

    async def test_empty_results(self, memory):
        mems = await memory.get_episodic_memories()
        assert mems == []

    async def test_table_created_alongside_existing(self, memory):
        """Verify episodic_memories table coexists with conversations/facts tables."""
        await memory.add_message("user", "test")
        await memory.set_fact("key", "val")
        mem_id = await memory.add_episodic_memory("manual", "coexist")
        assert mem_id > 0

        msgs = await memory.get_recent_messages()
        assert len(msgs) == 1
        facts = await memory.get_all_facts()
        assert facts == {"key": "val"}


# ── MemoryManager session lifecycle tests ──

@pytest.mark.asyncio
class TestMemoryManagerSessions:
    async def test_start_session(self, memory_manager):
        sid = memory_manager.start_session()
        assert sid is not None
        assert len(sid) == 12
        assert memory_manager.session_active is True

    async def test_end_session_empty_conversation(self, memory_manager):
        memory_manager.start_session()
        result = await memory_manager.end_session([])
        assert result is None
        assert memory_manager.session_active is False

    async def test_end_session_short_conversation(self, memory_manager):
        memory_manager.start_session()
        result = await memory_manager.end_session([{"role": "user", "content": "hi"}])
        assert result is None

    async def test_end_session_extracts_summary(self, memory_manager):
        memory_manager.start_session()

        mock_response = MagicMock()
        mock_response.message.content = json.dumps({
            "summary": "User discussed 3D printing project",
            "topics": ["3D printing", "robotics"],
            "decisions": ["Use PLA material"],
            "preferences": ["Prefers local solutions"],
            "intentions": ["Finish robotic arm by Friday"],
            "action_items": ["Order new filament"],
        })

        mock_client = MagicMock()
        mock_client.chat = AsyncMock(return_value=mock_response)
        memory_manager._MemoryManager__client = mock_client

        conversation = [
            {"role": "user", "content": "I'm working on my 3D printing project"},
            {"role": "assistant", "content": "What are you printing?"},
            {"role": "user", "content": "A robotic arm. I want to finish it by Friday"},
            {"role": "assistant", "content": "Good plan. What material?"},
            {"role": "user", "content": "PLA. I prefer local solutions for everything"},
        ]
        result = await memory_manager.end_session(conversation)

        assert result is not None
        assert "summary" in result

        all_mems = await memory_manager.memory.get_episodic_memories()
        types = {m["memory_type"] for m in all_mems}
        assert "session_summary" in types
        assert "preference" in types
        assert "intention" in types

    async def test_end_session_handles_markdown_fences(self, memory_manager):
        memory_manager.start_session()

        mock_response = MagicMock()
        mock_response.message.content = '```json\n{"summary": "test", "topics": [], "decisions": [], "preferences": [], "intentions": [], "action_items": []}\n```'

        mock_client = MagicMock()
        mock_client.chat = AsyncMock(return_value=mock_response)
        memory_manager._MemoryManager__client = mock_client

        result = await memory_manager.end_session([
            {"role": "user", "content": "Hello there"},
            {"role": "assistant", "content": "Hi! How can I help you today?"},
        ])

        assert result is not None
        assert result["summary"] == "test"

    async def test_end_session_handles_llm_failure(self, memory_manager):
        memory_manager.start_session()

        mock_client = MagicMock()
        mock_client.chat = AsyncMock(side_effect=Exception("LLM offline"))
        memory_manager._MemoryManager__client = mock_client

        result = await memory_manager.end_session([
            {"role": "user", "content": "test message with enough content to proceed"},
            {"role": "assistant", "content": "response that is long enough"},
        ])

        assert result is None
        assert memory_manager.session_active is False

    async def test_store_manual_memory(self, memory_manager):
        memory_manager.start_session()
        mem_id = await memory_manager.store_manual_memory("המשתמש אוהב פיצה")
        assert mem_id > 0

        mems = await memory_manager.memory.get_episodic_memories(memory_type="manual")
        assert len(mems) == 1
        assert mems[0]["content"] == "המשתמש אוהב פיצה"

    async def test_recall_episodic_empty(self, memory_manager):
        results = await memory_manager.recall_episodic("anything")
        assert results == []

    async def test_get_session_context_empty(self, memory_manager):
        ctx = await memory_manager.get_session_context("hello")
        assert ctx == ""


# ── MemorySkill tests ──

@pytest.mark.asyncio
class TestMemorySkill:
    async def test_remember(self, memory_manager):
        skill = MemorySkill(memory_manager)
        result = await skill.execute("remember", {"content": "I like pizza"})
        assert result["status"] == "ok"
        assert "memory_id" in result

        mems = await memory_manager.memory.get_episodic_memories(memory_type="manual")
        assert len(mems) == 1

    async def test_list_recent(self, memory_manager):
        skill = MemorySkill(memory_manager)

        await memory_manager.store_manual_memory("Memory 1")
        await memory_manager.store_manual_memory("Memory 2")

        result = await skill.execute("list_recent", {"limit": 10})
        assert result["status"] == "ok"
        assert result["count"] == 2

    async def test_list_recent_empty(self, memory_manager):
        skill = MemorySkill(memory_manager)
        result = await skill.execute("list_recent", {})
        assert result["status"] == "empty"

    async def test_forget(self, memory_manager):
        skill = MemorySkill(memory_manager)
        r = await skill.execute("remember", {"content": "temp memory"})
        mem_id = r["memory_id"]

        result = await skill.execute("forget", {"memory_id": mem_id})
        assert result["status"] == "ok"

        mems = await memory_manager.memory.get_episodic_memories()
        assert len(mems) == 0

    async def test_forget_nonexistent(self, memory_manager):
        skill = MemorySkill(memory_manager)
        result = await skill.execute("forget", {"memory_id": 99999})
        assert "error" in result

    async def test_recall_empty(self, memory_manager):
        skill = MemorySkill(memory_manager)
        result = await skill.execute("recall", {"query": "pizza"})
        assert result["status"] == "empty"

    async def test_unknown_action(self, memory_manager):
        skill = MemorySkill(memory_manager)
        result = await skill.execute("nonexistent_action", {})
        assert "error" in result

    async def test_skill_metadata(self, memory_manager):
        skill = MemorySkill(memory_manager)
        assert skill.name == "memory"
        actions = skill.get_actions()
        assert "remember" in actions
        assert "recall" in actions
        assert "list_recent" in actions
        assert "forget" in actions

    async def test_tool_definitions(self, memory_manager):
        skill = MemorySkill(memory_manager)
        tools = skill.as_tools()
        names = {t["function"]["name"] for t in tools}
        assert "memory_remember" in names
        assert "memory_recall" in names
        assert "memory_list_recent" in names
        assert "memory_forget" in names
