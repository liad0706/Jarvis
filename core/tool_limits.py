"""Tool result size limits — adapted from Claude Code's toolLimits.ts.

Prevents excessively large tool results from consuming the entire LLM context window.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# ── Constants (from Claude Code) ──────────────────────────────────────────────

# Max chars for a single tool result before truncation
MAX_RESULT_SIZE_CHARS: int = 50_000

# Max aggregate chars for all tool results in one round
MAX_RESULTS_PER_ROUND_CHARS: int = 200_000

# Max chars for tool summary in compact views (dashboard, logs)
TOOL_SUMMARY_MAX_LENGTH: int = 50

# Error message truncation limit (keep first + last N chars)
ERROR_TRUNCATE_LIMIT: int = 10_000


def truncate_tool_result(result: dict, max_chars: int = MAX_RESULT_SIZE_CHARS) -> dict:
    """Truncate a tool result dict if its JSON representation exceeds max_chars.

    Returns the original dict if small enough, or a truncated copy with a
    '[truncated]' marker.
    """
    try:
        serialized = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        serialized = str(result)

    if len(serialized) <= max_chars:
        return result

    logger.info(
        "Tool result truncated: %d → %d chars",
        len(serialized), max_chars,
    )

    # Try to preserve structure: truncate the longest string value
    truncated = _truncate_largest_value(result, max_chars)
    if truncated is not None:
        return truncated

    # Fallback: raw string truncation
    half = max_chars // 2
    preview = serialized[:half] + f"\n\n... [{len(serialized) - max_chars} chars truncated] ...\n\n" + serialized[-half:]
    return {"_truncated": True, "preview": preview}


def truncate_error(error_text: str, max_chars: int = ERROR_TRUNCATE_LIMIT) -> str:
    """Truncate an error message, keeping first and last halves.

    Adapted from Claude Code's formatError in toolErrors.ts.
    """
    if len(error_text) <= max_chars:
        return error_text
    half = max_chars // 2
    return (
        error_text[:half]
        + f"\n\n... [{len(error_text) - max_chars} characters truncated] ...\n\n"
        + error_text[-half:]
    )


def truncate_round_results(
    results: list[dict],
    max_total_chars: int = MAX_RESULTS_PER_ROUND_CHARS,
) -> list[dict]:
    """Shrink the largest results in a round until total is under budget."""
    serialized = []
    for r in results:
        try:
            s = json.dumps(r, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            s = str(r)
        serialized.append(s)

    total = sum(len(s) for s in serialized)
    if total <= max_total_chars:
        return results

    # Sort by size descending, truncate largest first
    indexed = sorted(enumerate(serialized), key=lambda x: len(x[1]), reverse=True)
    output = list(results)

    for idx, s in indexed:
        if total <= max_total_chars:
            break
        excess = total - max_total_chars
        current_len = len(s)
        # Shrink this result to fit, but never below 500 chars
        target = max(500, current_len - excess)
        if target < current_len:
            output[idx] = truncate_tool_result(results[idx], max_chars=target)
            new_len = len(json.dumps(output[idx], ensure_ascii=False, default=str))
            total -= (current_len - new_len)

    logger.info("Round results truncated: %d results, total now under %d chars", len(results), max_total_chars)
    return output


def summarize_tool_result(result: dict, max_length: int = TOOL_SUMMARY_MAX_LENGTH) -> str:
    """One-line summary of a tool result for compact display."""
    status = result.get("status", "")
    error = result.get("error", "")
    if error:
        text = f"error: {error}"
    elif status:
        text = f"{status}"
    else:
        text = str(result)
    if len(text) > max_length:
        return text[: max_length - 3] + "..."
    return text


# ── Internals ─────────────────────────────────────────────────────────────────

def _truncate_largest_value(d: dict, max_chars: int) -> dict | None:
    """Find the largest string value in a flat dict and truncate it."""
    if not isinstance(d, dict):
        return None

    largest_key = None
    largest_len = 0
    for k, v in d.items():
        if isinstance(v, str) and len(v) > largest_len:
            largest_key = k
            largest_len = len(v)

    if largest_key is None or largest_len < 200:
        return None

    # Calculate how much to cut
    try:
        current_total = len(json.dumps(d, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return None

    excess = current_total - max_chars
    if excess <= 0:
        return dict(d)

    new_len = max(100, largest_len - excess - 100)  # margin
    truncated = dict(d)
    half = new_len // 2
    original = truncated[largest_key]
    truncated[largest_key] = (
        original[:half] + f"\n[...{largest_len - new_len} chars truncated...]\n" + original[-half:]
    )
    truncated["_truncated"] = True
    return truncated
