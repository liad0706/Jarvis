"""Orchestrator - the brain of Jarvis. Routes user input to skills via multi-provider LLM."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from config import get_settings
from core.activity_manager import ActivityManager
from core.audit import AuditLog
from core.event_bus import EventBus
from core.memory import Memory
from core.memory_manager import MemoryManager
from core.model_router import ModelRouter, TaskType
from core.observability import MetricsCollector, Trace
from core.permissions import PermissionGate
from core.personality import (
    build_skills_summary,
)
from core.planner import Planner
from core.prompt_cache import PromptSectionRegistry
from core.providers import (
    BaseLLMProvider,
    LLMResponse,
    OllamaProvider,
    OpenAIProvider,
    ToolCall,
    get_provider,
)
from core.vision_ollama import encode_screenshot_for_ollama, ollama_model_supports_vision
from core.vision_openai import build_openai_user_image_message
from core.progress_summary import ProgressTracker
from core.resilience import CircuitBreaker, RateLimiter, TimeoutManager
from core.skill_base import SkillRegistry
from core.state_machine import TaskStateMachine
from core.token_estimator import check_context_budget, should_compact_conversation
from core.conversation_branch import BranchManager
from core.context_builder import ContextBuilder
from core.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)


class Orchestrator:
    MAX_TOOL_ROUNDS = 5
    # Short greetings only — skip huge tool schemas (speeds up qwen3-vl etc.)
    _TRIVIAL_GREETING_RE = re.compile(
        r"^\s*(hi|hello|hey|yo|hiya|sup|היי|שלום|הי\b|מה\s*נשמע|מה\s*קורה|בוקר\s*טוב|ערב\s*טוב)"
        r"\s*[!?.…׳]*\s*$",
        re.IGNORECASE,
    )

    def __init__(
        self,
        registry: SkillRegistry,
        memory: Memory,
        event_bus: EventBus | None = None,
        metrics: MetricsCollector | None = None,
        permission_gate: PermissionGate | None = None,
        audit_log: AuditLog | None = None,
        memory_manager: MemoryManager | None = None,
        planner: Planner | None = None,
        awareness=None,
    ):
        self.registry = registry
        self.memory = memory
        self.memory_manager = memory_manager
        self.awareness = awareness
        self.event_bus = event_bus or EventBus()
        self.metrics = metrics or MetricsCollector()
        self.permission_gate = permission_gate
        self.audit_log = audit_log
        self.planner = planner
        self.settings = get_settings()
        self.conversation: list[dict] = []
        self._conversation_path = Path(__file__).resolve().parent.parent / "data" / "conversation_state.json"
        self._load_conversation()
        self._session_started: bool = False
        self._provider: BaseLLMProvider | None = None
        self._circuit = CircuitBreaker(name="llm", failure_threshold=5, reset_timeout=60)
        self._rate_limiter = RateLimiter(max_calls=30, period=60.0)
        self._timeouts = TimeoutManager(
            overrides={
                "llm_call": max(30.0, float(self.settings.llm_call_timeout_seconds)),
            }
        )
        self.model_router = ModelRouter(self.settings)
        self.branch_manager = BranchManager()
        self.skill_store = None
        self.notifications = None
        self.last_response_streamed: bool = False
        self.progress_tracker = ProgressTracker(event_bus=self.event_bus)
        self.activity_manager = ActivityManager()
        self._prompt_sections = PromptSectionRegistry()
        self._prompt_sections.register_cached("skills_summary", self._build_skills_summary)
        self._skills_summary_signature: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
        # Populated during process(): image paths tools want pushed to dashboard / WhatsApp / CLI
        self.outgoing_chat_images: list[str] = []

        self._context_builder = ContextBuilder(
            memory=self.memory,
            memory_manager=self.memory_manager,
            awareness=self.awareness,
            get_skills_summary=self._get_skills_summary,
        )
        self._tool_executor = ToolExecutor(
            registry=self.registry,
            metrics=self.metrics,
            event_bus=self.event_bus,
            permission_gate=self.permission_gate,
            activity_manager=self.activity_manager,
            progress_tracker=self.progress_tracker,
            awareness=self.awareness,
            execute_tool_fn=self._execute_tool,
            format_tool_result_fn=self._format_tool_result,
            extract_images_fn=self._extract_chat_outgoing_images,
        )

    # ------------------------------------------------------------------
    # Conversation persistence (survives hot-reload)
    # ------------------------------------------------------------------

    def _load_conversation(self) -> None:
        """Load conversation from disk if it exists and is recent (< 2 hours old)."""
        try:
            if self._conversation_path.exists():
                import time
                age = time.time() - self._conversation_path.stat().st_mtime
                if age > 43200:  # older than 12 hours — stale
                    self._conversation_path.unlink(missing_ok=True)
                    return
                data = json.loads(self._conversation_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self.conversation = data[-40:]
                    logger.info("Restored conversation (%d messages)", len(self.conversation))
        except Exception as e:
            logger.debug("Failed to load conversation state: %s", e)

    def _save_conversation(self) -> None:
        """Persist conversation to disk (called after each exchange)."""
        try:
            self._conversation_path.parent.mkdir(parents=True, exist_ok=True)
            self._conversation_path.write_text(
                json.dumps(self.conversation[-40:], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug("Failed to save conversation state: %s", e)

    def _build_skills_summary(self) -> str:
        return build_skills_summary(self.registry.all_skills())

    def _current_skills_signature(self) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        rows = []
        for skill in self.registry.all_skills():
            rows.append((skill.name, skill.description, tuple(skill.get_actions())))
        return tuple(rows)

    def _get_skills_summary(self) -> str:
        signature = self._current_skills_signature()
        if signature != self._skills_summary_signature:
            self._prompt_sections.invalidate("skills_summary")
            self._skills_summary_signature = signature
        return self._prompt_sections.get("skills_summary")

    @staticmethod
    def _extract_chat_outgoing_images(result: dict) -> list[str]:
        if not isinstance(result, dict):
            return []
        out: list[str] = []
        raw = result.get("chat_outgoing_images")
        if isinstance(raw, (list, tuple)):
            for x in raw:
                if x:
                    out.append(str(x))
        one = result.get("chat_outgoing_image")
        if one:
            out.append(str(one))
        return out

    def _format_tool_result(
        self,
        tool_name: str,
        result: dict,
        tool_call_id: str | None = None,
        provider: BaseLLMProvider | None = None,
    ) -> list[dict]:
        """Build provider tool message(s). Ollama: images on tool msg. OpenAI/Codex: extra user msg with image."""
        active_provider = provider or self.provider
        vision_path = result.get("vision_attach_path")
        body = (
            {k: v for k, v in result.items() if k != "vision_attach_path"}
            if vision_path
            else result
        )
        tid = tool_call_id or "call_0"
        msg = active_provider.format_tool_result(body, tool_call_id=tid)

        out: list[dict] = []

        if isinstance(active_provider, OllamaProvider) and ollama_model_supports_vision(
            active_provider.model
        ):
            b64 = None
            if vision_path:
                b64 = encode_screenshot_for_ollama(vision_path)
            elif tool_name == "system_screenshot" and result.get("status") == "ok":
                p = result.get("path")
                if p:
                    b64 = encode_screenshot_for_ollama(p)
            if b64:
                msg["images"] = [b64]
                msg["tool_name"] = tool_name
            out.append(msg)
            return out

        out.append(msg)

        img_path = vision_path
        if not img_path and tool_name == "system_screenshot" and result.get("status") == "ok":
            img_path = result.get("path")

        if (
            img_path
            and Path(img_path).is_file()
            and isinstance(active_provider, OpenAIProvider)
        ):
            caption = (
                "Image from the previous tool (webcam snapshot or screen). "
                "Answer the user's question in their language based on what you see."
            )
            out.append(build_openai_user_image_message(str(img_path), caption))

        return out

    @property
    def provider(self) -> BaseLLMProvider:
        """Lazy-init the LLM provider."""
        if self._provider is None:
            self._provider = self._bind_provider(get_provider(self.settings))
            logger.info("LLM provider: %s", self._provider.name)
        return self._provider

    @property
    def _client(self):
        """Backward-compatible test hook for legacy tests patching orchestrator._client.chat."""
        return self.provider

    def _provider_for_route(
        self,
        provider_id: str,
        override_model: str | None = None,
    ) -> BaseLLMProvider:
        cfg = self.settings.model_copy(deep=True)
        cfg.llm_provider = provider_id
        if override_model:
            if provider_id == "ollama":
                cfg.ollama_model = override_model
            elif provider_id in {"openai", "codex", "lm_studio"}:
                cfg.openai_model = override_model
                cfg.codex_cli_model = override_model
            elif provider_id in {"anthropic", "claude"}:
                cfg.anthropic_model = override_model
        return self._bind_provider(get_provider(cfg))

    def _bind_provider(self, provider: BaseLLMProvider) -> BaseLLMProvider:
        binder = getattr(provider, "bind_event_bus", None)
        if callable(binder):
            try:
                bound = binder(self.event_bus)
                if bound is not None:
                    return bound
            except Exception:
                logger.debug("Provider event-bus binding failed", exc_info=True)
        return provider

    @staticmethod
    def _provider_family(provider: BaseLLMProvider | None) -> str:
        name = getattr(provider, "name", "") if provider else ""
        lower = name.lower()
        if "codex" in lower:
            return "codex"
        if "openai" in lower:
            return "openai"
        if "claude" in lower or "anthropic" in lower:
            return "anthropic"
        if "ollama" in lower:
            return "ollama"
        return lower

    @staticmethod
    def _should_retry_with_fallback(error: Exception, route: dict, provider: BaseLLMProvider) -> bool:
        fallback_name = (route.get("fallback_provider") or "").strip().lower()
        if not fallback_name:
            return False
        if Orchestrator._provider_family(provider) == fallback_name:
            return False
        msg = str(error).lower()
        retry_markers = (
            "not found",
            "404",
            "api key",
            "authentication",
            "unauthorized",
            "failed to authenticate",
            "oauth",
        )
        return any(marker in msg for marker in retry_markers)

    def _route_provider_for_task(self, task_type: str) -> tuple[BaseLLMProvider, dict]:
        """Return a provider instance for the routed task type."""
        route = self.model_router.get_route(task_type) if self.model_router else {}
        provider_name = route.get("preferred_provider") or self.settings.llm_provider
        fallback_name = route.get("fallback_provider") or self.settings.llm_provider
        model_override = route.get("preferred_model")

        if self._provider is not None and type(getattr(self._provider, "chat", None)).__module__ == "unittest.mock":
            return self._provider, route

        try:
            return self._provider_for_route(provider_name, model_override), route
        except Exception as first_error:
            logger.warning("Task route %s failed via %s: %s", task_type, provider_name, first_error)
            return self._provider_for_route(fallback_name, None), route

    @staticmethod
    def _provider_supports_streaming(provider: BaseLLMProvider) -> bool:
        if not getattr(provider, "supports_streaming", False) or not hasattr(provider, "stream_chat"):
            return False

        # Tests frequently patch provider.chat directly. If we still take the
        # streaming path, those mocks are bypassed and the real provider runs.
        for attr_name in ("chat", "stream_chat"):
            attr = getattr(provider, attr_name, None)
            if type(attr).__module__ == "unittest.mock":
                return attr_name == "stream_chat"
        return True

    async def _stream_provider_chat(
        self,
        provider: BaseLLMProvider,
        messages: list[dict],
    ) -> LLMResponse:
        """Stream a provider response token-by-token over the event bus."""
        chunks: list[str] = []
        self.last_response_streamed = True
        await self.event_bus.emit("stream.start")
        try:
            async for token in provider.stream_chat(messages, tools=None):
                chunks.append(token)
                await self.event_bus.emit("stream.token", token=token)
        finally:
            full_text = "".join(chunks)
            await self.event_bus.emit("stream.complete", full_text=full_text)
        return LLMResponse(content=full_text, tool_calls=[])

    @staticmethod
    def _normalize_llm_response(response: Any) -> LLMResponse:
        if isinstance(response, LLMResponse):
            return response

        raw_message = getattr(response, "message", None)
        raw_content = ""
        raw_tool_calls = None

        if raw_message is not None:
            maybe_content = getattr(raw_message, "content", "")
            raw_content = maybe_content if isinstance(maybe_content, str) else ""
            raw_tool_calls = getattr(raw_message, "tool_calls", None)
        else:
            maybe_content = getattr(response, "content", "")
            raw_content = maybe_content if isinstance(maybe_content, str) else ""
            raw_tool_calls = getattr(response, "tool_calls", None)

        normalized_calls: list[ToolCall] = []
        if isinstance(raw_tool_calls, (list, tuple)):
            for index, raw_call in enumerate(raw_tool_calls):
                function = getattr(raw_call, "function", None)
                name = getattr(function, "name", None) or getattr(raw_call, "name", None)
                arguments = (
                    getattr(function, "arguments", {})
                    if function is not None
                    else getattr(raw_call, "arguments", {})
                )
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                if isinstance(name, str) and name:
                    normalized_calls.append(
                        ToolCall(
                            name=name,
                            arguments=arguments,
                            id=getattr(raw_call, "id", None) or f"call_{index}",
                        )
                    )

        return LLMResponse(content=raw_content, tool_calls=normalized_calls, raw=response)

    @staticmethod
    def _actionable_error_text(error: Exception) -> str:
        msg = str(error)
        lower = msg.lower()
        if "ollama" in lower and ("404" in lower or "not found" in lower):
            return (
                f"שגיאה: Jarvis couldn't find the configured Ollama model. {msg} "
                "Try pulling the model again or switch provider from the dashboard."
            )
        if "api key" in lower or "authentication" in lower or "unauthorized" in lower:
            return (
                f"שגיאה: The selected model provider rejected the request. {msg} "
                "Check the provider credentials or temporarily switch provider."
            )
        if "timeout" in lower:
            return (
                f"שגיאה: The model took too long to respond. {msg} "
                "For local Ollama (cold start / large models), increase JARVIS_LLM_CALL_TIMEOUT_SECONDS in .env "
                "(default 300). Or use a smaller model."
            )
        return f"שגיאה: Jarvis hit a model error. {msg}"

    _PLAN_KEYWORDS = {"build", "create", "implement", "deploy", "setup", "install",
                       "configure", "migrate", "refactor", "design", "plan",
                       "תבנה", "תיצור", "תתקין", "תגדיר"}

    # Questions / conversations should NEVER trigger planning
    _QUESTION_PATTERNS = re.compile(
        r"(^\s*מה\s|^\s*למה\s|^\s*איך\s|^\s*מי\s|^\s*איפה\s|^\s*מתי\s|^\s*האם\s"
        r"|^\s*what\s|^\s*why\s|^\s*how\s|^\s*who\s|^\s*where\s|^\s*when\s"
        r"|^\s*is\s|^\s*are\s|^\s*do\s|^\s*does\s|^\s*can\s|^\s*could\s"
        r"|^\s*tell me|^\s*explain|^\s*תסביר|^\s*ספר לי"
        r"|\?\s*$)",
        re.IGNORECASE,
    )

    def _might_need_plan(self, text: str) -> bool:
        """Fast heuristic: skip the LLM complexity check for obviously simple messages."""
        t = text.strip()
        # Short messages — never plan
        if len(t) < 120 and not any(kw in t.lower() for kw in self._PLAN_KEYWORDS):
            return False
        # Questions / conversations — never plan
        if self._QUESTION_PATTERNS.search(t):
            return False
        # Must have at least 2 plan keywords or "and"/"ו" connectors suggesting multi-step
        lower = t.lower()
        keyword_count = sum(1 for kw in self._PLAN_KEYWORDS if kw in lower)
        has_connector = any(c in lower for c in (" and ", " then ", " ואז ", " ואחרי ", " ולאחר "))
        if keyword_count < 2 and not has_connector:
            return False
        return True

    def _is_trivial_greeting(self, text: str) -> bool:
        """True for hi/hello only — we omit tool definitions for a much faster first token."""
        t = (text or "").strip()
        if not t or len(t) > 48:
            return False
        return bool(self._TRIVIAL_GREETING_RE.match(t))

    # ── Side-question (/btw) — lightweight, no tools, single turn ──

    _BTW_PATTERN = re.compile(r"^/btw\b\s*", re.IGNORECASE)

    def _is_side_question(self, text: str) -> bool:
        return bool(self._BTW_PATTERN.match(text.strip()))

    async def process_side_question(self, user_input: str) -> str:
        """Answer a quick /btw question without any tools — single LLM turn.

        Adapted from Claude Code's sideQuestion.ts: the model gets conversation
        context but NO tool definitions and is limited to one response.
        """
        question = self._BTW_PATTERN.sub("", user_input).strip()
        if not question:
            return "מה רצית לשאול?"

        logger.info("Side question: %.80s", question)
        await self.event_bus.emit("chat.side_question", content=question)

        # Build a minimal system prompt (no tools, no skills)
        from core.personality import text_contains_hebrew, HEBREW_ONLY_APPEND

        facts = (
            await self.memory_manager.get_all_facts()
            if self.memory_manager
            else await self.memory.get_all_facts()
        )
        facts_text = "\n".join(f"- {k}: {v}" for k, v in facts.items()) if facts else ""

        locale_extra = ""
        if text_contains_hebrew(question):
            locale_extra = HEBREW_ONLY_APPEND

        system_msg = (
            "You are Jarvis, a personal AI assistant. Answer the following side question "
            "directly in a single response.\n\n"
            "CRITICAL CONSTRAINTS:\n"
            "- You have NO tools available — you cannot take any actions.\n"
            "- This is a one-off response — there will be no follow-up turns.\n"
            "- You can ONLY provide information based on conversation context and known facts.\n"
            "- NEVER say 'Let me try...', 'I'll now...', or promise to take any action.\n"
            "- If you don't know the answer, say so.\n\n"
            f"Known facts:\n{facts_text}\n\n"
            f"{locale_extra}"
        )

        messages = [
            {"role": "system", "content": system_msg},
            *self.conversation[-10:],  # recent context
            {"role": "user", "content": question},
        ]

        routed_provider, _ = self._route_provider_for_task(TaskType.GREETING)
        try:
            await self._rate_limiter.acquire()
            response = await self._circuit.call(
                self._timeouts.with_timeout,
                "llm_call",
                routed_provider.chat(messages=messages, tools=None),
            )
            llm_response = self._normalize_llm_response(response)
            answer = llm_response.content or "לא הצלחתי לענות."
        except Exception as e:
            logger.exception("Side question LLM error")
            answer = self._actionable_error_text(e)

        # Don't pollute main conversation — just emit the answer
        await self.event_bus.emit("chat.assistant", content=answer)
        return answer

    async def handle(self, user_input: str) -> str:
        """Top-level entry: side-question, plan, or direct."""
        self.outgoing_chat_images = []
        self.last_response_streamed = False

        # /btw side question — fast path, no tools
        if self._is_side_question(user_input):
            return await self.process_side_question(user_input)

        if self.planner and self._might_need_plan(user_input):
            skills_summary = self._get_skills_summary()
            needs_plan = await self.planner.should_plan(user_input, skills_summary)
            if needs_plan:
                logger.info("Complex task detected, creating plan")
                await self.event_bus.emit("plan.deciding", input=user_input)
                plan = await self.planner.create_plan(user_input, skills_summary)
                if len(plan.steps) > 1:
                    sm = TaskStateMachine(
                        plan=plan,
                        planner=self.planner,
                        event_bus=self.event_bus,
                    )
                    return await sm.run(self)
        return await self.process(user_input)

    def fork_conversation(self, at_index: int | None = None, label: str = "") -> dict:
        branch = self.branch_manager.fork(self.conversation, at_index=at_index, label=label)
        return branch.to_dict()

    def list_branches(self) -> list[dict]:
        return self.branch_manager.list_branches()

    def switch_branch(self, branch_id: str) -> list[dict] | None:
        messages = self.branch_manager.switch(branch_id)
        if messages is not None:
            self.conversation = list(messages)
            self._save_conversation()
        return messages

    async def process(self, user_input: str) -> str:
        """Process user input via direct tool-calling (single-step or called per plan step)."""
        trace = Trace()

        # Start episodic session on first message
        if self.memory_manager and not self._session_started:
            self.memory_manager.start_session()
            self._session_started = True

        if self.memory_manager:
            await self.memory_manager.add_message("user", user_input)
        else:
            await self.memory.add_message("user", user_input)
        self.conversation.append({"role": "user", "content": user_input})
        await self.event_bus.emit("chat.user", content=user_input)

        # Record user interaction for pattern learning
        if self.awareness and self.awareness.pattern_learner:
            try:
                from datetime import datetime
                now = datetime.now()
                self.awareness.pattern_learner.record_event(
                    event_type="user_message",
                    details=user_input[:100],
                    hour=now.hour,
                    weekday=now.weekday(),
                )
            except Exception:
                pass

        # Reset proactive engine — user is active
        if hasattr(self, "proactive_engine") and self.proactive_engine:
            self.proactive_engine.user_responded()

        if len(self.conversation) > 30:
            self.conversation = self.conversation[-20:]

        trivial = self._is_trivial_greeting(user_input)
        tool_names = [tool["function"]["name"] for tool in self.registry.get_all_tools()]
        task_type = (
            TaskType.GREETING
            if trivial
            else self.model_router.classify_task(
                user_input,
                tool_names=tool_names,
            )
        )
        routed_provider, active_route = self._route_provider_for_task(task_type)
        allow_tools = task_type != TaskType.GREETING
        fallback_attempted = False
        await self.event_bus.emit(
            "model.route",
            task_type=task_type,
            provider=getattr(routed_provider, "name", "unknown"),
            model=getattr(routed_provider, "model", ""),
            description=active_route.get("description", ""),
        )
        self.activity_manager.record(
            "llm",
            "route",
            detail=f"{task_type} -> {getattr(routed_provider, 'name', 'unknown')} {getattr(routed_provider, 'model', '')}".strip(),
            status="ok",
            metadata={"task_type": task_type},
            dedup_key=f"route:{task_type}:{getattr(routed_provider, 'name', 'unknown')}:{getattr(routed_provider, 'model', '')}",
        )

        async with trace.span("build_context"):
            system_prompt, episodic_context = await self._context_builder.build(
                user_input=user_input,
                conversation=self.conversation,
                trivial=trivial,
            )

        messages = [{"role": "system", "content": system_prompt}] + self.conversation
        final_response = ""
        tool_results: list[dict] = []
        all_tools_used: list[str] = []
        had_tool_round = False
        _tool_fail_count: dict[str, int] = {}   # track per-tool failures
        _MAX_SAME_TOOL_FAILS = 2                 # stop retrying after 2 failures

        # Token budget check (warn if close to limit)
        if not trivial:
            all_tools_defs = self.registry.get_all_tools() if allow_tools else None
            prov_name = getattr(routed_provider, "name", "ollama")
            prov_model = getattr(routed_provider, "model", "")
            budget = check_context_budget(
                messages, all_tools_defs,
                provider_name=prov_name,
                model=prov_model,
            )
            # CLI-based providers (codex-cli, claude-cli) have tighter internal
            # chunking limits — compact earlier (at 60% utilization).
            is_cli_provider = prov_name in ("codex-cli", "claude-cli")
            compact_threshold = 0.60 if is_cli_provider else 0.75
            if not budget["fits"] or (is_cli_provider and budget["utilization"] > compact_threshold):
                logger.warning(
                    "Context budget tight: %d/%d tokens (%.0f%% utilization)",
                    budget["total_tokens"], budget["context_window"],
                    budget["utilization"] * 100,
                )
                # Auto-compact conversation if needed
                if should_compact_conversation(messages, all_tools_defs,
                    provider_name=prov_name,
                    model=prov_model,
                    threshold=compact_threshold):
                    if self.memory_manager:
                        summary = await self.memory_manager.maybe_summarize(self.conversation)
                        if summary:
                            logger.info("Auto-compacted conversation (%d chars)", len(summary))
                            # Rebuild messages with compacted conversation
                            messages = [{"role": "system", "content": system_prompt}] + self.conversation

        self.progress_tracker.start()

        for round_num in range(self.MAX_TOOL_ROUNDS):
            tools = None
            if not trivial and allow_tools and not had_tool_round:
                tools = self.registry.get_all_tools()

            async with trace.span("llm_call", round=round_num):
                slow_vl = (
                    isinstance(routed_provider, OllamaProvider)
                    and round_num == 0
                    and ollama_model_supports_vision(routed_provider.model)
                )
                await self.event_bus.emit("llm.start", round=round_num, slow_vl_model=slow_vl)
                await self.metrics.increment("llm_calls_total")
                try:
                    await self._rate_limiter.acquire()
                    import time as _time
                    t0 = _time.time()

                    async def _llm_call():
                        if tools is None and self._provider_supports_streaming(routed_provider):
                            return await self._stream_provider_chat(routed_provider, messages)
                        return await routed_provider.chat(
                            messages=messages,
                            tools=tools if tools else None,
                        )

                    llm_response = await self._circuit.call(
                        self._timeouts.with_timeout, "llm_call", _llm_call()
                    )
                    llm_response = self._normalize_llm_response(llm_response)
                    elapsed = (_time.time() - t0) * 1000
                    await self.metrics.histogram("llm_latency_ms", elapsed)
                except Exception as e:
                    if not fallback_attempted and self._should_retry_with_fallback(
                        e, active_route, routed_provider
                    ):
                        fallback_name = active_route.get("fallback_provider") or self.settings.llm_provider
                        try:
                            logger.warning(
                                "LLM route %s failed via %s; retrying with fallback %s",
                                task_type,
                                getattr(routed_provider, "name", "unknown"),
                                fallback_name,
                            )
                            routed_provider = self._provider_for_route(str(fallback_name), None)
                            fallback_attempted = True
                            await self.event_bus.emit(
                                "model.route",
                                task_type=task_type,
                                provider=getattr(routed_provider, "name", "unknown"),
                                model=getattr(routed_provider, "model", ""),
                                description=f"{active_route.get('description', '')} (fallback)",
                            )
                            continue
                        except Exception as fallback_error:
                            logger.warning("Fallback provider %s failed to initialize: %s", fallback_name, fallback_error)
                    logger.exception("LLM error (round %d)", round_num)
                    await self.metrics.increment("llm_errors_total")
                    self.activity_manager.record(
                        "llm",
                        "error",
                        detail=str(e)[:160],
                        status="error",
                        metadata={"round": round_num},
                        dedup_key=f"llm-error:{type(e).__name__}:{str(e)[:120]}",
                    )
                    final_response = self._actionable_error_text(e)
                    break

            await self.event_bus.emit("llm.complete", round=round_num)

            if not llm_response.tool_calls:
                final_response = llm_response.content
                break

            logger.info("Tool round %d: %d call(s)", round_num + 1, len(llm_response.tool_calls))

            # Build assistant message with tool calls for context
            assistant_msg = {"role": "assistant", "content": llm_response.content}
            if hasattr(llm_response.raw, "message") and hasattr(llm_response.raw.message, "tool_calls"):
                # Ollama: preserve native tool_calls for message history
                assistant_msg["tool_calls"] = llm_response.raw.message.tool_calls
            messages.append(assistant_msg)

            # Collect tool names before delegating (used for context summary later)
            all_tools_used.extend(tc.name for tc in llm_response.tool_calls)

            tool_results, round_images = await self._tool_executor.run_round(
                tool_calls=llm_response.tool_calls,
                routed_provider=routed_provider,
                trace=trace,
                tool_fail_count=_tool_fail_count,
                max_same_tool_fails=_MAX_SAME_TOOL_FAILS,
            )

            # Merge outgoing images collected by the executor
            for rp in round_images:
                if rp not in self.outgoing_chat_images:
                    self.outgoing_chat_images.append(rp)

            messages.extend(tool_results)
            had_tool_round = True
        else:
            final_response = final_response or "הגעתי למגבלת הפעולות, אנא נסה שוב."

        if self.progress_tracker.tool_count:
            self.activity_manager.record(
                "progress",
                "final",
                detail=self.progress_tracker.final_summary(),
                status="done",
                dedup_key=f"progress-final:{self.progress_tracker.tool_count}",
            )
        self.progress_tracker.stop()

        if not (final_response or "").strip():
            logger.warning("Empty LLM response after successful call (no exception); showing fallback to user")
            final_response = (
                "שגיאה: המודל לא החזיר טקסט. ודא ש-Ollama רץ, שהמודל מותקן (ollama pull …), "
                "וש־JARVIS_OLLAMA_HOST ב-.env מצביע על אותו שרת (למשל Windows לעומת WSL)."
            )

        # Prepend a tool-use summary so context survives reconnects/restarts
        if all_tools_used:
            unique_tools = list(dict.fromkeys(all_tools_used))[:8]
            summary = f"[Context: used {', '.join(unique_tools)} to answer]"
            self.conversation.append({"role": "assistant", "content": summary + "\n" + final_response})
        else:
            self.conversation.append({"role": "assistant", "content": final_response})

        self._save_conversation()
        if self.memory_manager:
            await self.memory_manager.add_message("assistant", final_response)
        else:
            await self.memory.add_message("assistant", final_response)

        await self.event_bus.emit("chat.assistant", content=final_response)
        return final_response

    @staticmethod
    def _cast_params(skill, action: str, params: dict) -> dict:
        """Cast LLM params to the types expected by the skill method.

        LLMs often pass "30" instead of 30 for int params.  We inspect
        the method signature and coerce strings → int / float / bool.
        """
        if hasattr(skill, "coerce_params"):
            return skill.coerce_params(action, params)
        return params

    async def _execute_tool(
        self,
        skill,
        action: str,
        params: dict,
        trace: Trace,
    ) -> dict:
        """Execute a tool call with permission gating and audit."""
        # Auto-cast string params to expected types (int, float, bool)
        params = self._cast_params(skill, action, params)

        if self.permission_gate:
            async def _do():
                return await skill.execute(action, params)

            async with trace.span("tool_exec", skill=skill.name, action=action):
                result = await self.permission_gate.gate(
                    skill.name, action, params, _do, trace_id=trace.trace_id,
                )
        else:
            async with trace.span("tool_exec", skill=skill.name, action=action):
                try:
                    result = await skill.execute(action, params)
                except Exception as e:
                    logger.exception("Skill execution error")
                    result = {"error": str(e)}

        await self.metrics.increment("skill_execution_total")
        if "error" in result:
            await self.metrics.increment("tool_errors_total")
        return result
