"""ContextBuilder — assembles system prompt and episodic context for Orchestrator.process()."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.memory import Memory
    from core.memory_manager import MemoryManager
    from core.personality import build_system_prompt  # noqa: F401 (type hint only)

from core.personality import (
    build_system_prompt,
    build_trivial_greeting_prompt,
)

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Builds (system_prompt, episodic_context) for a single Orchestrator turn.

    All heavy I/O (fact fetching, memory retrieval, awareness snapshot) is
    parallelised with asyncio.gather so the total latency is bounded by the
    slowest single call rather than the sum of all calls.
    """

    def __init__(
        self,
        memory: "Memory",
        memory_manager=None,
        awareness=None,
        get_skills_summary=None,
    ):
        self.memory = memory
        self.memory_manager = memory_manager
        self.awareness = awareness
        # Callable returning the current skills-summary string (injected from Orchestrator)
        self._get_skills_summary = get_skills_summary

    async def build(
        self,
        user_input: str,
        conversation: list[dict],
        trivial: bool,
    ) -> tuple[str, str]:
        """Return ``(system_prompt, episodic_context)``.

        Parameters
        ----------
        user_input:
            The raw user message for this turn.
        conversation:
            Current conversation history list (used only for maybe_summarize).
        trivial:
            True when the message is a greeting — skips tools/env context.
        """
        if self.memory_manager:
            facts, relevant, episodic_context, summary = await asyncio.gather(
                self.memory_manager.get_all_facts(),
                self.memory_manager.get_relevant_history(user_input, top_k=3),
                self.memory_manager.get_session_context(user_input, top_k=5),
                self.memory_manager.maybe_summarize(conversation),
            )
            memory_context = ""
            if relevant:
                memory_context += relevant
            if summary:
                memory_context += f"\n\nConversation summary (older messages): {summary}"
        else:
            facts = await self.memory.get_all_facts()
            memory_context = ""
            episodic_context = ""

        # Environment awareness — inject live state into prompt
        env_context = ""
        if self.awareness and not trivial:
            try:
                snap = await self.awareness.snapshot(include_discoveries=False)
                env_context = self.awareness.format_for_prompt(snap)
            except Exception as e:
                logger.debug("awareness snapshot failed: %s", e)

        if trivial:
            system_prompt = build_trivial_greeting_prompt(
                facts,
                memory_context,
                episodic_context=episodic_context,
                user_message_for_locale=user_input,
            )
        else:
            skills_summary = (
                self._get_skills_summary() if callable(self._get_skills_summary) else ""
            )
            system_prompt = build_system_prompt(
                skills_summary,
                facts,
                memory_context,
                episodic_context=episodic_context,
                env_context=env_context,
                user_message_for_locale=user_input,
            )

        return system_prompt, episodic_context
