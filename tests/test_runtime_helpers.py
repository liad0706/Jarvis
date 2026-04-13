import json

import pytest

from core.event_bus import EventBus
from core.progress_summary import ProgressTracker
from core.token_estimator import check_context_budget, should_compact_conversation
from core.tool_hooks import ToolHookRegistry
from core.tool_limits import truncate_error, truncate_round_results, truncate_tool_result


@pytest.mark.asyncio
async def test_tool_hook_registry_can_modify_and_observe_calls():
    registry = ToolHookRegistry()
    seen = []

    @registry.pre_hook("dummy.greet")
    async def inject_name(skill_name, action, params):
        return {"params": {"name": "Jarvis"}}

    @registry.post_hook("dummy.greet")
    async def remember_result(skill_name, action, params, result, duration_ms):
        seen.append((params["name"], result["status"], duration_ms))

    params = {}
    mods = await registry.run_pre_hooks("dummy", "greet", params)
    await registry.run_post_hooks("dummy", "greet", params, {"status": "ok"}, 12.5)

    assert mods["params_modified"] is True
    assert params["name"] == "Jarvis"
    assert seen == [("Jarvis", "ok", 12.5)]


@pytest.mark.asyncio
async def test_tool_hook_registry_can_block_calls():
    registry = ToolHookRegistry()

    async def block_all(skill_name, action, params):
        return {"block": True, "reason": "maintenance"}

    registry.add_pre_hook("dummy.*", block_all, name="maintenance_gate")

    mods = await registry.run_pre_hooks("dummy", "greet", {})

    assert mods == {"blocked": True, "reason": "maintenance"}


def test_tool_limit_helpers_truncate_large_payloads():
    result = {"status": "ok", "output": "x" * 5000}
    truncated = truncate_tool_result(result, max_chars=700)

    assert truncated["_truncated"] is True
    assert len(json.dumps(truncated, ensure_ascii=False, default=str)) <= 900

    round_results = truncate_round_results(
        [{"a": "x" * 1200}, {"b": "y" * 1200}],
        max_total_chars=1000,
    )
    total_chars = sum(len(json.dumps(item, ensure_ascii=False, default=str)) for item in round_results)
    assert total_chars <= 1000

    error = truncate_error("boom" * 1000, max_chars=120)
    assert "truncated" in error


def test_token_estimator_flags_tight_context_budget():
    messages = [{"role": "user", "content": "x" * 25000}]
    tools = [{"function": {"name": "dummy", "description": "y" * 8000, "parameters": {}}}]

    budget = check_context_budget(messages, tools, provider_name="ollama", model="qwen2.5:7b")

    assert budget["total_tokens"] > 0
    assert budget["fits"] is False
    assert should_compact_conversation(messages, tools, provider_name="ollama", model="qwen2.5:7b") is True


@pytest.mark.asyncio
async def test_progress_tracker_emits_summary_event():
    event_bus = EventBus()
    summaries = []

    async def on_progress(summary=None, **kwargs):
        summaries.append(summary)

    event_bus.on("task.progress", on_progress)

    tracker = ProgressTracker(event_bus=event_bus)
    tracker.start()
    tracker.record_tool("dummy", "greet", success=True, duration_ms=18.0)
    tracker._last_summary_at = 0

    summary = await tracker.maybe_emit_summary()

    assert summary is not None
    assert "dummy.greet" in summary
    assert summaries == [summary]
