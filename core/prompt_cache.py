"""Memoized / lazy prompt sections — adapted from Claude Code's systemPromptSections.ts.

Caches expensive prompt sections (skills summary, env context) so they don't
recompute on every turn. Sections that change per-turn (time, facts) are marked
volatile and always recompute.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Cache TTL in seconds — static sections rebuild at most this often
DEFAULT_TTL: float = 60.0


class CachedSection:
    """A memoized prompt section with TTL."""

    __slots__ = ("_builder", "_value", "_built_at", "_ttl", "_name")

    def __init__(self, name: str, builder: Callable[[], str], ttl: float = DEFAULT_TTL):
        self._name = name
        self._builder = builder
        self._value: str | None = None
        self._built_at: float = 0
        self._ttl = ttl

    def get(self) -> str:
        now = time.time()
        if self._value is None or (now - self._built_at) > self._ttl:
            self._value = self._builder()
            self._built_at = now
            logger.debug("Prompt section '%s' rebuilt (%d chars)", self._name, len(self._value))
        return self._value

    def invalidate(self) -> None:
        self._value = None
        self._built_at = 0


class VolatileSection:
    """A prompt section that always recomputes (no caching)."""

    __slots__ = ("_builder", "_name")

    def __init__(self, name: str, builder: Callable[[], str]):
        self._name = name
        self._builder = builder

    def get(self) -> str:
        return self._builder()

    def invalidate(self) -> None:
        pass  # no-op, always fresh


class PromptSectionRegistry:
    """Registry of prompt sections, each cached or volatile."""

    def __init__(self):
        self._sections: dict[str, CachedSection | VolatileSection] = {}

    def register_cached(self, name: str, builder: Callable[[], str], ttl: float = DEFAULT_TTL) -> None:
        self._sections[name] = CachedSection(name, builder, ttl)

    def register_volatile(self, name: str, builder: Callable[[], str]) -> None:
        self._sections[name] = VolatileSection(name, builder)

    def get(self, name: str) -> str:
        section = self._sections.get(name)
        if section is None:
            logger.warning("Unknown prompt section: %s", name)
            return ""
        return section.get()

    def get_all(self) -> dict[str, str]:
        return {name: section.get() for name, section in self._sections.items()}

    def invalidate(self, name: str) -> None:
        section = self._sections.get(name)
        if section:
            section.invalidate()

    def invalidate_all(self) -> None:
        for section in self._sections.values():
            section.invalidate()

    @property
    def section_names(self) -> list[str]:
        return list(self._sections.keys())
