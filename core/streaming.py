"""Streaming response support — token-by-token output via EventBus."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

logger = logging.getLogger(__name__)


class StreamBuffer:
    """Collects streaming tokens and broadcasts them via EventBus."""

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._buffer: list[str] = []
        self._complete = False
        self._session_id: str = ""

    async def push_token(self, token: str, session_id: str = ""):
        """Push a single token to the stream."""
        self._buffer.append(token)
        self._session_id = session_id
        if self.event_bus:
            await self.event_bus.emit(
                "stream.token",
                token=token,
                session_id=session_id,
                buffer_length=len(self._buffer),
            )

    async def complete(self, full_text: str = "", session_id: str = ""):
        """Mark the stream as complete."""
        self._complete = True
        if not full_text:
            full_text = "".join(self._buffer)
        if self.event_bus:
            await self.event_bus.emit(
                "stream.complete",
                full_text=full_text,
                session_id=session_id,
            )

    def get_full_text(self) -> str:
        return "".join(self._buffer)

    def reset(self):
        self._buffer = []
        self._complete = False
        self._session_id = ""


class StreamingMixin:
    """Mixin for LLM providers that support streaming.

    Providers can implement stream_chat() to yield tokens one at a time.
    The orchestrator checks if the provider has this method and uses it
    when streaming is enabled.
    """

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """Override in provider to yield tokens. Default: not supported."""
        raise NotImplementedError("This provider does not support streaming")

    @property
    def supports_streaming(self) -> bool:
        """Check if this provider implements streaming."""
        try:
            # Check if stream_chat is overridden (not the base NotImplementedError version)
            return type(self).stream_chat is not StreamingMixin.stream_chat
        except AttributeError:
            return False


class OllamaStreamingMixin(StreamingMixin):
    """Streaming support for Ollama provider."""

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """Stream tokens from Ollama."""
        kwargs = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        # Don't stream when tools are present (need full response for tool calls)
        if tools:
            raise NotImplementedError("Cannot stream with tool calls")

        if "qwen" in self.model.lower():
            kwargs["options"] = {"temperature": 0.35}

        async for chunk in await self.client.chat(**kwargs):
            content = chunk.get("message", {}).get("content", "")
            if content:
                yield content


class OpenAIStreamingMixin(StreamingMixin):
    """Streaming support for OpenAI provider."""

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """Stream tokens from OpenAI."""
        if tools:
            raise NotImplementedError("Cannot stream with tool calls")

        # Clean messages same as non-streaming
        clean_msgs = []
        for m in messages:
            if m["role"] == "user" and isinstance(m.get("content"), list):
                msg = {"role": "user", "content": m["content"]}
            else:
                msg = {"role": m["role"], "content": m.get("content", "")}
            if m["role"] == "tool":
                msg["tool_call_id"] = m.get("tool_call_id", "call_0")
            clean_msgs.append(msg)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=clean_msgs,
            stream=True,
        )

        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content
