"""Tests for the async event bus."""

import pytest

from core.event_bus import EventBus


@pytest.mark.asyncio
class TestEventBus:
    async def test_on_and_emit(self, event_bus):
        results = []

        async def handler(value=None):
            results.append(value)

        event_bus.on("test", handler)
        await event_bus.emit("test", value=42)

        assert results == [42]

    async def test_multiple_listeners(self, event_bus):
        results = []

        async def handler_a(**kwargs):
            results.append("a")

        async def handler_b(**kwargs):
            results.append("b")

        event_bus.on("event", handler_a)
        event_bus.on("event", handler_b)
        await event_bus.emit("event")

        assert results == ["a", "b"]

    async def test_off_removes_listener(self, event_bus):
        results = []

        async def handler(**kwargs):
            results.append(1)

        event_bus.on("event", handler)
        event_bus.off("event", handler)
        await event_bus.emit("event")

        assert results == []

    async def test_emit_nonexistent_event(self, event_bus):
        # Should not raise
        await event_bus.emit("nonexistent", data="test")

    async def test_emit_collect(self, event_bus):
        async def handler_1(**kwargs):
            return 10

        async def handler_2(**kwargs):
            return 20

        event_bus.on("calc", handler_1)
        event_bus.on("calc", handler_2)

        results = await event_bus.emit_collect("calc")
        assert results == [10, 20]

    async def test_listener_exception_does_not_crash(self, event_bus):
        results = []

        async def bad_handler(**kwargs):
            raise ValueError("boom")

        async def good_handler(**kwargs):
            results.append("ok")

        event_bus.on("event", bad_handler)
        event_bus.on("event", good_handler)

        # Should not raise, and good handler should still run
        await event_bus.emit("event")
        assert results == ["ok"]

    async def test_emit_collect_with_exception(self, event_bus):
        async def bad(**kwargs):
            raise RuntimeError("fail")

        async def good(**kwargs):
            return 99

        event_bus.on("ev", bad)
        event_bus.on("ev", good)

        results = await event_bus.emit_collect("ev")
        assert results == [99]
