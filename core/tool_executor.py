"""ToolExecutor — runs one round of tool calls on behalf of Orchestrator.process()."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.activity_manager import ActivityManager
    from core.event_bus import EventBus
    from core.observability import MetricsCollector, Trace
    from core.permissions import PermissionGate
    from core.progress_summary import ProgressTracker
    from core.providers import BaseLLMProvider, ToolCall
    from core.skill_base import SkillRegistry

from core.skill_colors import get_color_for_dashboard
from core.tool_hooks import hook_registry
from core.tool_limits import (
    truncate_error,
    truncate_round_results,
    truncate_tool_result,
    summarize_tool_result,
)

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes a list of LLM-requested tool calls and returns formatted results.

    A single ``run_round()`` call handles one complete tool round:
    - resolve each call via the skill registry
    - check permissions / run pre+post hooks
    - truncate / summarise oversized results
    - extract outgoing image paths
    - record progress & activity
    """

    def __init__(
        self,
        registry: "SkillRegistry",
        metrics: "MetricsCollector",
        event_bus: "EventBus",
        permission_gate: "PermissionGate | None",
        activity_manager: "ActivityManager",
        progress_tracker: "ProgressTracker",
        awareness=None,
        # Callables injected from Orchestrator to avoid circular deps
        execute_tool_fn=None,
        format_tool_result_fn=None,
        extract_images_fn=None,
    ):
        self.registry = registry
        self.metrics = metrics
        self.event_bus = event_bus
        self.permission_gate = permission_gate
        self.activity_manager = activity_manager
        self.progress_tracker = progress_tracker
        self.awareness = awareness
        # These are bound methods from Orchestrator so we keep full behaviour
        self._execute_tool = execute_tool_fn
        self._format_tool_result = format_tool_result_fn
        self._extract_chat_outgoing_images = extract_images_fn

    async def run_round(
        self,
        tool_calls: list["ToolCall"],
        routed_provider: "BaseLLMProvider",
        trace: "Trace",
        tool_fail_count: dict[str, int],
        max_same_tool_fails: int = 2,
    ) -> tuple[list[dict], list[str]]:
        """Execute *tool_calls* and return ``(tool_results, image_paths)``.

        Parameters
        ----------
        tool_calls:
            Tool calls from the current LLM response.
        routed_provider:
            The provider currently in use (needed for ``format_tool_result``).
        trace:
            Active observability trace (spans are created per tool).
        tool_fail_count:
            Mutable dict shared across rounds — tracks per-tool failure counts.
        max_same_tool_fails:
            How many failures of the same tool trigger the "stop retrying" hint.
        """
        tool_results: list[dict] = []
        image_paths: list[str] = []

        for tc in tool_calls:
            func_name = tc.name
            func_args = tc.arguments
            tc_id = getattr(tc, "id", None) or "call_0"

            logger.info("Tool call: %s(%s)", func_name, func_args)
            await self.event_bus.emit("tool.start", tool=func_name, args=func_args)
            await self.metrics.increment("tool_calls_total")

            result: dict = {}
            resolved = self.registry.resolve_tool_call(func_name)
            if resolved:
                skill, action = resolved

                # ── Pre-hooks ──
                hook_mods = await hook_registry.run_pre_hooks(skill.name, action, func_args)
                if hook_mods.get("blocked"):
                    result = {"error": f"Blocked by hook: {hook_mods.get('reason', '')}"}
                else:
                    import time as _time
                    t_tool = _time.time()
                    result = await self._execute_tool(skill, action, func_args, trace)
                    tool_duration_ms = (_time.time() - t_tool) * 1000

                    # ── Truncate large results ──
                    result = truncate_tool_result(result)

                    # ── Truncate error text ──
                    if "error" in result and isinstance(result["error"], str):
                        result["error"] = truncate_error(result["error"])

                    # ── Post-hooks ──
                    await hook_registry.run_post_hooks(
                        skill.name, action, func_args, result, tool_duration_ms,
                    )

                    # ── Progress tracking ──
                    self.progress_tracker.record_tool(
                        skill.name, action,
                        success="error" not in result,
                        duration_ms=tool_duration_ms,
                    )
                    progress_summary = await self.progress_tracker.maybe_emit_summary()
                    if progress_summary:
                        self.activity_manager.record(
                            "progress",
                            "summary",
                            detail=progress_summary,
                            status="info",
                            dedup_key=progress_summary,
                        )

                # ── Collect outgoing images ──
                for img_path in self._extract_chat_outgoing_images(result):
                    try:
                        rp = str(Path(img_path).resolve())
                        if Path(rp).is_file() and rp not in image_paths:
                            image_paths.append(rp)
                        await self.event_bus.emit("chat.outgoing_image", path=rp)
                    except OSError:
                        logger.debug("Invalid outgoing image path: %s", img_path)

                tool_results.extend(
                    self._format_tool_result(
                        func_name,
                        result,
                        tool_call_id=tc_id,
                        provider=routed_provider,
                    )
                )
            else:
                await self.metrics.increment("tool_errors_total")
                tool_results.append(
                    routed_provider.format_tool_result(
                        {"error": f"Unknown tool: {func_name}"},
                        tool_call_id=tc_id,
                    )
                )

            # Track per-tool failures to prevent retry loops
            if "error" in result:
                tool_fail_count[func_name] = tool_fail_count.get(func_name, 0) + 1
                if tool_fail_count[func_name] >= max_same_tool_fails:
                    result["error"] += (
                        f" | הכלי {func_name} נכשל {tool_fail_count[func_name]} פעמים."
                        " אל תנסה שוב את אותו כלי — תודיע למשתמש על השגיאה."
                    )

            await self.event_bus.emit(
                "tool.complete",
                tool=func_name,
                has_error="error" in result,
                color=get_color_for_dashboard(func_name),
            )
            self.activity_manager.record(
                "tool",
                func_name,
                detail=summarize_tool_result(result),
                status="error" if "error" in result else "ok",
                metadata={"color": get_color_for_dashboard(func_name)},
                dedup_key=f"tool:{func_name}:{'error' if 'error' in result else 'ok'}:{summarize_tool_result(result)}",
            )

            # Record in action journal
            if self.awareness and self.awareness.action_journal:
                try:
                    params_short = str(func_args)[:80] if func_args else ""
                    result_short = str(
                        result.get("reply_to_user_hebrew")
                        or result.get("detail")
                        or result.get("status", "")
                    )[:80]
                    self.awareness.action_journal.record(
                        action_type="tool_call",
                        action_name=func_name,
                        params_summary=params_short,
                        result_summary=result_short,
                        success="error" not in result,
                    )
                except Exception:
                    pass

        # ── Truncate aggregate round results if too large ──
        tool_results = truncate_round_results(tool_results)

        return tool_results, image_paths
