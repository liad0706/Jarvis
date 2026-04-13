"""Tool hooks system — pre/post callbacks on every tool call.

Adapted from Claude Code's toolHooks.ts. Allows plugins, logging, dashboards,
and external integrations to observe or modify tool execution without touching
the orchestrator.

Usage:
    from core.tool_hooks import ToolHookRegistry, hook_registry

    @hook_registry.pre_hook("*")          # all tools
    async def log_all(skill, action, params):
        logger.info("About to run %s.%s", skill, action)

    @hook_registry.post_hook("smart_home_*")   # glob pattern
    async def notify_ha(skill, action, params, result):
        await push_to_dashboard(result)
"""

from __future__ import annotations

import fnmatch
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

PreHookFn = Callable[..., Coroutine[Any, Any, dict | None]]
PostHookFn = Callable[..., Coroutine[Any, Any, dict | None]]


@dataclass
class HookEntry:
    pattern: str  # fnmatch glob for "skill_name.action" or "*"
    callback: PreHookFn | PostHookFn
    name: str = ""


class ToolHookRegistry:
    """Central registry for pre/post tool-call hooks."""

    def __init__(self):
        self._pre_hooks: list[HookEntry] = []
        self._post_hooks: list[HookEntry] = []

    # ── Registration ──────────────────────────────────────────────────────

    def pre_hook(self, pattern: str = "*", name: str = ""):
        """Decorator to register a pre-tool hook."""
        def decorator(fn: PreHookFn) -> PreHookFn:
            self._pre_hooks.append(HookEntry(pattern=pattern, callback=fn, name=name or fn.__name__))
            return fn
        return decorator

    def post_hook(self, pattern: str = "*", name: str = ""):
        """Decorator to register a post-tool hook."""
        def decorator(fn: PostHookFn) -> PostHookFn:
            self._post_hooks.append(HookEntry(pattern=pattern, callback=fn, name=name or fn.__name__))
            return fn
        return decorator

    def add_pre_hook(self, pattern: str, callback: PreHookFn, name: str = "") -> None:
        self._pre_hooks.append(HookEntry(pattern=pattern, callback=callback, name=name))

    def add_post_hook(self, pattern: str, callback: PostHookFn, name: str = "") -> None:
        self._post_hooks.append(HookEntry(pattern=pattern, callback=callback, name=name))

    def clear(self) -> None:
        self._pre_hooks.clear()
        self._post_hooks.clear()

    # ── Execution ─────────────────────────────────────────────────────────

    async def run_pre_hooks(
        self,
        skill_name: str,
        action: str,
        params: dict,
    ) -> dict:
        """Run all matching pre-hooks. Returns aggregated modifications.

        A pre-hook can return:
            None — no change
            {"block": True, "reason": "..."} — block execution
            {"params": {...}} — override params
        """
        key = f"{skill_name}.{action}"
        mods: dict = {}

        for entry in self._pre_hooks:
            if not fnmatch.fnmatch(key, entry.pattern) and entry.pattern != "*":
                continue
            try:
                result = await entry.callback(skill_name, action, params)
                if isinstance(result, dict):
                    if result.get("block"):
                        logger.info("Pre-hook '%s' blocked %s: %s", entry.name, key, result.get("reason", ""))
                        return {"blocked": True, "reason": result.get("reason", "Blocked by hook")}
                    if "params" in result:
                        params.update(result["params"])
                        mods["params_modified"] = True
            except Exception:
                logger.exception("Pre-hook '%s' failed for %s", entry.name, key)

        return mods

    async def run_post_hooks(
        self,
        skill_name: str,
        action: str,
        params: dict,
        result: dict,
        duration_ms: float = 0,
    ) -> None:
        """Run all matching post-hooks (fire-and-forget, no blocking).

        A post-hook receives skill_name, action, params, result, duration_ms.
        """
        key = f"{skill_name}.{action}"

        for entry in self._post_hooks:
            if not fnmatch.fnmatch(key, entry.pattern) and entry.pattern != "*":
                continue
            try:
                await entry.callback(skill_name, action, params, result, duration_ms)
            except Exception:
                logger.exception("Post-hook '%s' failed for %s", entry.name, key)


# ── Singleton ─────────────────────────────────────────────────────────────────
hook_registry = ToolHookRegistry()
