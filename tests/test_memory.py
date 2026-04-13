"""Tests for the SQLite memory system."""

import pytest

from core.memory import Memory


@pytest.mark.asyncio
class TestMemory:
    async def test_add_and_get_messages(self, memory):
        await memory.add_message("user", "Hello")
        await memory.add_message("assistant", "Hi there!")

        messages = await memory.get_recent_messages(limit=10)
        assert len(messages) == 2
        assert messages[0] == {"role": "user", "content": "Hello"}
        assert messages[1] == {"role": "assistant", "content": "Hi there!"}

    async def test_get_recent_messages_limit(self, memory):
        for i in range(10):
            await memory.add_message("user", f"Message {i}")

        messages = await memory.get_recent_messages(limit=3)
        assert len(messages) == 3
        # Should be the last 3 messages
        assert messages[0]["content"] == "Message 7"
        assert messages[1]["content"] == "Message 8"
        assert messages[2]["content"] == "Message 9"

    async def test_get_recent_messages_empty(self, memory):
        messages = await memory.get_recent_messages()
        assert messages == []

    async def test_set_and_get_fact(self, memory):
        await memory.set_fact("name", "Jarvis")
        result = await memory.get_fact("name")
        assert result == "Jarvis"

    async def test_get_nonexistent_fact(self, memory):
        result = await memory.get_fact("nonexistent")
        assert result is None

    async def test_update_fact(self, memory):
        await memory.set_fact("version", "1.0")
        await memory.set_fact("version", "2.0")
        result = await memory.get_fact("version")
        assert result == "2.0"

    async def test_get_all_facts(self, memory):
        await memory.set_fact("a", "1")
        await memory.set_fact("b", "2")
        await memory.set_fact("c", "3")

        facts = await memory.get_all_facts()
        assert facts == {"a": "1", "b": "2", "c": "3"}

    async def test_get_all_facts_empty(self, memory):
        facts = await memory.get_all_facts()
        assert facts == {}

    async def test_hebrew_content(self, memory):
        await memory.add_message("user", "שלום עולם")
        await memory.set_fact("barber", "ישי פרץ")

        messages = await memory.get_recent_messages()
        assert messages[0]["content"] == "שלום עולם"

        barber = await memory.get_fact("barber")
        assert barber == "ישי פרץ"

    async def test_close_and_reopen(self, tmp_path):
        db_path = tmp_path / "reopen_test.db"

        mem1 = Memory(db_path=db_path)
        await mem1.init()
        await mem1.set_fact("key", "value")
        await mem1.close()

        mem2 = Memory(db_path=db_path)
        await mem2.init()
        result = await mem2.get_fact("key")
        await mem2.close()

        assert result == "value"
