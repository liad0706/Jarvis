"""Unit tests for Ollama stream chunk parsing (content vs thinking)."""

from types import SimpleNamespace

import pytest

from core.providers import _ollama_stream_message_delta


def test_delta_dict_content_only():
    chunk = {"message": {"role": "assistant", "content": "Hello"}}
    assert _ollama_stream_message_delta(chunk) == "Hello"


def test_delta_dict_thinking_when_content_empty():
    chunk = {"message": {"role": "assistant", "content": "", "thinking": "step 1"}}
    assert _ollama_stream_message_delta(chunk) == "step 1"


def test_delta_dict_prefers_content_over_thinking():
    chunk = {"message": {"content": "out", "thinking": "hidden"}}
    assert _ollama_stream_message_delta(chunk) == "out"


def test_delta_object_like_ollama_chatresponse():
    """Pydantic ChatResponse uses attribute access on nested Message."""
    msg = SimpleNamespace(content="Hi", thinking=None)
    chunk = SimpleNamespace(message=msg)
    assert _ollama_stream_message_delta(chunk) == "Hi"


def test_delta_object_thinking_fallback():
    msg = SimpleNamespace(content="", thinking="reasoning…")
    chunk = SimpleNamespace(message=msg)
    assert _ollama_stream_message_delta(chunk) == "reasoning…"


def test_delta_empty_message():
    assert _ollama_stream_message_delta({}) == ""
    assert _ollama_stream_message_delta({"message": None}) == ""


@pytest.mark.asyncio
async def test_stream_chat_collects_thinking_chunks(monkeypatch):
    """OllamaProvider.stream_chat should yield thinking when content is empty."""

    async def fake_stream():
        yield {"message": {"role": "assistant", "content": "", "thinking": "a"}}
        yield {"message": {"role": "assistant", "content": "", "thinking": "b"}}
        yield {"message": {"role": "assistant", "content": "done", "thinking": ""}}

    class FakeClient:
        async def chat(self, **kwargs):
            assert kwargs.get("stream") is True
            return fake_stream()

    from core.providers import OllamaProvider

    p = OllamaProvider(host="http://127.0.0.1:11434", model="test")
    p._client = FakeClient()
    parts = []
    async for piece in p.stream_chat([{"role": "user", "content": "x"}]):
        parts.append(piece)
    assert parts == ["a", "b", "done"]
