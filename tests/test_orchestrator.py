"""Tests for the orchestrator (with mocked Ollama)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.memory import Memory
from core.model_router import TaskType
from core.orchestrator import Orchestrator
from core.providers import LLMResponse
from core.skill_base import SkillRegistry
from tests.conftest import DummySkill


def _make_ollama_response(content="Hello!", tool_calls=None):
    """Create a mock Ollama chat response."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    resp = MagicMock()
    resp.message = msg
    return resp


def _make_tool_call(name, arguments=None):
    tc = MagicMock()
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = arguments or {}
    return tc


@pytest.mark.asyncio
class TestOrchestrator:
    async def test_simple_chat(self, memory):
        registry = SkillRegistry()
        orchestrator = Orchestrator(registry, memory)

        mock_response = _make_ollama_response(content="Hi there!")

        with patch.object(orchestrator._client, "chat", new_callable=AsyncMock, return_value=mock_response):
            result = await orchestrator.process("Hello")

        assert result == "Hi there!"

    async def test_conversation_saved_to_memory(self, memory):
        registry = SkillRegistry()
        orchestrator = Orchestrator(registry, memory)

        mock_response = _make_ollama_response(content="Response")

        with patch.object(orchestrator._client, "chat", new_callable=AsyncMock, return_value=mock_response):
            await orchestrator.process("Test message")

        messages = await memory.get_recent_messages()
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Test message"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Response"

    async def test_tool_call_execution(self, memory):
        registry = SkillRegistry()
        dummy = DummySkill()
        registry.register(dummy)
        orchestrator = Orchestrator(registry, memory)

        tool_call = _make_tool_call("dummy_greet", {"name": "Jarvis"})
        first_response = _make_ollama_response(content="", tool_calls=[tool_call])
        follow_up = _make_ollama_response(content="I greeted Jarvis for you!")

        call_count = 0

        async def mock_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_response
            return follow_up

        with patch.object(orchestrator._client, "chat", side_effect=mock_chat):
            result = await orchestrator.process("Greet Jarvis")

        assert result == "I greeted Jarvis for you!"
        assert call_count == 2

    async def test_tool_call_unknown_tool(self, memory):
        registry = SkillRegistry()
        orchestrator = Orchestrator(registry, memory)

        tool_call = _make_tool_call("unknown_tool", {})
        first_response = _make_ollama_response(content="", tool_calls=[tool_call])
        follow_up = _make_ollama_response(content="I couldn't find that tool.")

        call_count = 0

        async def mock_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_response
            return follow_up

        with patch.object(orchestrator._client, "chat", side_effect=mock_chat):
            result = await orchestrator.process("Do something")

        assert "couldn't find" in result.lower() or call_count == 2

    async def test_tool_call_skill_exception(self, memory):
        registry = SkillRegistry()
        dummy = DummySkill()
        # Patch execute to raise
        dummy.execute = AsyncMock(side_effect=RuntimeError("Skill crashed"))
        registry.register(dummy)
        orchestrator = Orchestrator(registry, memory)

        tool_call = _make_tool_call("dummy_greet", {})
        first_response = _make_ollama_response(content="", tool_calls=[tool_call])
        follow_up = _make_ollama_response(content="There was an error.")

        call_count = 0

        async def mock_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_response
            return follow_up

        with patch.object(orchestrator._client, "chat", side_effect=mock_chat):
            result = await orchestrator.process("Greet")

        # Should not crash, should return some response
        assert isinstance(result, str)

    async def test_ollama_connection_error(self, memory):
        registry = SkillRegistry()
        orchestrator = Orchestrator(registry, memory)

        with patch.object(
            orchestrator._client, "chat",
            new_callable=AsyncMock,
            side_effect=ConnectionError("No Ollama"),
        ):
            result = await orchestrator.process("Hello")

        assert "שגיאה" in result

    async def test_conversation_window_trimming(self, memory):
        registry = SkillRegistry()
        orchestrator = Orchestrator(registry, memory)

        mock_response = _make_ollama_response(content="ok")

        with patch.object(orchestrator._client, "chat", new_callable=AsyncMock, return_value=mock_response):
            # Add 35 messages (exceeds window of 30)
            for i in range(35):
                orchestrator.conversation.append({"role": "user", "content": f"msg {i}"})

            await orchestrator.process("final message")

        # Should be trimmed to ~20 + the new user+assistant messages
        assert len(orchestrator.conversation) <= 25

    async def test_vision_route_retries_with_fallback_provider(self, memory):
        registry = SkillRegistry()
        orchestrator = Orchestrator(registry, memory)

        class MissingVisionProvider:
            name = "ollama"
            model = "qwen3-vl"

            async def chat(self, messages, tools=None):
                raise RuntimeError("Ollama model 'qwen3-vl' not found at http://127.0.0.1:11434 (404)")

        class FallbackProvider:
            name = "codex-oauth"
            model = "gpt-5.4"

            async def chat(self, messages, tools=None):
                return LLMResponse(content="אני יכול להסביר על יכולות הראייה שלי גם בלי לצלם עכשיו.", tool_calls=[])

        failing = MissingVisionProvider()
        fallback = FallbackProvider()

        orchestrator.model_router.classify_task = lambda user_input, has_images=False, tool_names=None: TaskType.VISION  # type: ignore[method-assign]
        orchestrator._route_provider_for_task = lambda task_type: (  # type: ignore[method-assign]
            failing,
            {
                "description": "Vision-capable model",
                "fallback_provider": "codex",
            },
        )
        orchestrator._provider_for_route = lambda provider_id, override_model=None: fallback  # type: ignore[method-assign]

        result = await orchestrator.process("מה אתה יכול לעשות עם המצלמה שלי?")

        assert "יכולות הראייה" in result
