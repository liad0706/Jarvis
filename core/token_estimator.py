"""Token estimation before LLM calls — adapted from Claude Code's analyzeContext.ts.

Estimates token count for messages + tools to prevent context overflow.
Uses a simple chars-per-token heuristic (no tokenizer dependency).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Conservative estimate: ~4 chars per token for English, ~2-3 for CJK/Hebrew
CHARS_PER_TOKEN: float = 3.5
BYTES_PER_TOKEN: int = 4

# Default context windows by provider (approximate)
CONTEXT_WINDOWS: dict[str, int] = {
    "ollama": 8192,      # varies by model, conservative default
    "openai": 128_000,
    "anthropic": 200_000,
    "codex": 128_000,
    "codex-cli": 128_000,
    "codex-oauth": 128_000,
    "claude-cli": 200_000,
    "lm_studio": 32_768,
}

# Reserve this fraction of context for the model's response
RESPONSE_RESERVE_FRACTION: float = 0.15


def estimate_tokens(text: str) -> int:
    """Estimate token count from a string."""
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def estimate_message_tokens(messages: list[dict]) -> int:
    """Estimate total tokens for a list of chat messages."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        # Role overhead (~4 tokens per message)
        total += 4
        # Tool calls in assistant messages
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            try:
                total += estimate_tokens(json.dumps(tool_calls, default=str))
            except (TypeError, ValueError):
                total += 50  # rough estimate
    return total


def estimate_tools_tokens(tools: list[dict] | None) -> int:
    """Estimate tokens consumed by tool definitions."""
    if not tools:
        return 0
    try:
        serialized = json.dumps(tools, ensure_ascii=False)
        return estimate_tokens(serialized)
    except (TypeError, ValueError):
        return len(tools) * 200  # rough fallback


def get_context_window(provider_name: str, model: str = "") -> int:
    """Get the context window size for a provider/model."""
    lower_provider = provider_name.lower()

    # Check for known large-context models
    lower_model = model.lower()
    if "128k" in lower_model or "gpt-4" in lower_model:
        return 128_000
    if "claude" in lower_model:
        return 200_000
    if "qwen" in lower_model and ("32b" in lower_model or "72b" in lower_model):
        return 32_768

    return CONTEXT_WINDOWS.get(lower_provider, 8192)


def check_context_budget(
    messages: list[dict],
    tools: list[dict] | None,
    provider_name: str = "ollama",
    model: str = "",
) -> dict:
    """Check if messages + tools fit within the context window.

    Returns:
        {
            "fits": bool,
            "message_tokens": int,
            "tool_tokens": int,
            "total_tokens": int,
            "context_window": int,
            "available_for_response": int,
            "utilization": float,  # 0.0 - 1.0
        }
    """
    msg_tokens = estimate_message_tokens(messages)
    tool_tokens = estimate_tools_tokens(tools)
    total = msg_tokens + tool_tokens

    window = get_context_window(provider_name, model)
    response_reserve = int(window * RESPONSE_RESERVE_FRACTION)
    available = window - total

    return {
        "fits": total < (window - response_reserve),
        "message_tokens": msg_tokens,
        "tool_tokens": tool_tokens,
        "total_tokens": total,
        "context_window": window,
        "available_for_response": max(0, available),
        "utilization": min(1.0, total / window) if window > 0 else 1.0,
    }


def should_compact_conversation(
    messages: list[dict],
    tools: list[dict] | None = None,
    provider_name: str = "ollama",
    model: str = "",
    threshold: float = 0.75,
) -> bool:
    """Return True if conversation should be compacted (summarized)."""
    budget = check_context_budget(messages, tools, provider_name, model)
    return budget["utilization"] > threshold
