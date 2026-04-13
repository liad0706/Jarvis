"""Async event bus for inter-module communication."""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

Listener = Callable[..., Coroutine[Any, Any, None]]


class EventBus:
    """Simple pub/sub event bus."""

    def __init__(self):
        self._listeners: dict[str, list[Listener]] = defaultdict(list)

    def on(self, event: str, callback: Listener) -> None:
        self._listeners[event].append(callback)

    def off(self, event: str, callback: Listener) -> None:
        self._listeners[event].remove(callback)

    async def emit(self, event: str, **kwargs) -> None:
        for cb in self._listeners.get(event, []):
            try:
                await cb(**kwargs)
            except Exception:
                logger.exception("Error in listener for event %s", event)

    async def emit_collect(self, event: str, **kwargs) -> list[Any]:
        """Emit and collect return values from all listeners."""
        results = []
        for cb in self._listeners.get(event, []):
            try:
                results.append(await cb(**kwargs))
            except Exception:
                logger.exception("Error in listener for event %s", event)
        return results
