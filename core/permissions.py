"""Permission gate — risk-level classification, approval prompts, dry-run support."""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, Callable, Coroutine

from core.async_input import async_input as _async_input
from core.audit import AuditEntry, AuditLog

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    READ = "read"
    WRITE = "write"
    EXTERNAL = "external"
    CRITICAL = "critical"

    def __ge__(self, other):
        order = [RiskLevel.READ, RiskLevel.WRITE, RiskLevel.EXTERNAL, RiskLevel.CRITICAL]
        return order.index(self) >= order.index(other)

    def __gt__(self, other):
        order = [RiskLevel.READ, RiskLevel.WRITE, RiskLevel.EXTERNAL, RiskLevel.CRITICAL]
        return order.index(self) > order.index(other)


DEFAULT_RISK_OVERRIDES: dict[str, dict[str, RiskLevel]] = {
    "code": {
        "write": RiskLevel.WRITE,
        "edit": RiskLevel.WRITE,
        "run": RiskLevel.CRITICAL,
        "list": RiskLevel.READ,
    },
    "self_improve": {
        "create": RiskLevel.WRITE,
        "list": RiskLevel.READ,
        "remove": RiskLevel.WRITE,
    },
    "appointment": {
        "book": RiskLevel.EXTERNAL,
        "check_available": RiskLevel.EXTERNAL,
    },
    "spotify": {
        "play": RiskLevel.EXTERNAL,
        "pause": RiskLevel.EXTERNAL,
        "current": RiskLevel.READ,
        "search": RiskLevel.EXTERNAL,
    },
    "creality": {
        "open": RiskLevel.WRITE,
        "import_model": RiskLevel.WRITE,
        "start_print": RiskLevel.CRITICAL,
    },
    "creality_api": {
        "status": RiskLevel.READ,
        "list_files": RiskLevel.READ,
        "get_camera_snapshot": RiskLevel.READ,
        "upload_gcode": RiskLevel.WRITE,
        "pause": RiskLevel.WRITE,
        "resume": RiskLevel.WRITE,
        "cancel": RiskLevel.CRITICAL,
    },
    "models": {
        "search": RiskLevel.EXTERNAL,
        "download": RiskLevel.EXTERNAL,
        "list": RiskLevel.READ,
    },
    "chat_image_sender": {
        "send_file_to_chat": RiskLevel.WRITE,
        "send_screen_capture_to_chat": RiskLevel.WRITE,
    },
    "apple_tv": {
        "discover": RiskLevel.READ,
        "status": RiskLevel.READ,
        "pairing_status": RiskLevel.READ,
        "pair_protocol": RiskLevel.EXTERNAL,
        # Home assistant: TV on/off like lights — auto-approve (not safe_mode-blocked)
        "power_off": RiskLevel.WRITE,
        "power_on": RiskLevel.WRITE,
    },
}


class PermissionGate:
    def __init__(
        self,
        audit_log: AuditLog,
        safe_mode: bool = False,
        dry_run: bool = False,
        auto_approve_external: bool = True,
        llm_classifier: LLMClassifier | None = None,
    ):
        self.audit_log = audit_log
        self.safe_mode = safe_mode
        self.dry_run = dry_run
        self.auto_approve = {RiskLevel.READ, RiskLevel.WRITE, RiskLevel.EXTERNAL, RiskLevel.CRITICAL}
        self._risk_overrides = dict(DEFAULT_RISK_OVERRIDES)
        self.llm_classifier = llm_classifier

    def classify_action(self, skill_name: str, action: str) -> RiskLevel:
        skill_map = self._risk_overrides.get(skill_name, {})
        if action in skill_map:
            return skill_map[action]
        return RiskLevel.WRITE

    async def request_approval(
        self,
        skill_name: str,
        action: str,
        params: dict | None = None,
        trace_id: str = "",
    ) -> bool:
        risk = self.classify_action(skill_name, action)
        desc = f"{skill_name}.{action}"

        if self.safe_mode and risk >= RiskLevel.EXTERNAL:
            logger.warning("BLOCKED by safe_mode: %s (risk=%s)", desc, risk.value)
            await self.audit_log.log(AuditEntry(
                actor="system",
                action=action,
                skill=skill_name,
                params_summary=_redact(params),
                risk_level=risk.value,
                approved_by="blocked",
                result_status="denied",
                trace_id=trace_id,
            ))
            return False

        if risk in self.auto_approve:
            return True

        # LLM classifier — if enabled, can auto-approve or auto-deny
        if self.llm_classifier and self.llm_classifier.is_enabled:
            verdict = await self.llm_classifier.classify(skill_name, action, params)
            if verdict == "ALLOW":
                logger.info("LLM auto-approved: %s", desc)
                return True
            if verdict == "DENY":
                logger.warning("LLM auto-denied: %s", desc)
                await self.audit_log.log(AuditEntry(
                    actor="llm_classifier",
                    action=action,
                    skill=skill_name,
                    params_summary=_redact(params),
                    risk_level=risk.value,
                    approved_by="llm_denied",
                    result_status="denied",
                    trace_id=trace_id,
                ))
                return False
            # verdict == "ASK" → fall through to manual prompt

        approved = await self._prompt_user(desc, risk)
        await self.audit_log.log(AuditEntry(
            actor="system",
            action=action,
            skill=skill_name,
            params_summary=_redact(params),
            risk_level=risk.value,
            approved_by="user" if approved else "denied",
            result_status="approved" if approved else "denied",
            trace_id=trace_id,
        ))
        return approved

    async def _prompt_user(self, description: str, risk: RiskLevel) -> bool:
        prompt = (
            f"\033[93m[Permission Required]\033[0m "
            f"\033[91m{risk.value.upper()}\033[0m risk action: \033[96m{description}\033[0m\n"
            f"  Allow? (y/n): "
        )
        try:
            try:
                answer = await asyncio.to_thread(input, prompt)
            except Exception:
                answer = await _async_input(prompt)
            return answer.strip().lower() in ("y", "yes", "כן")
        except (EOFError, KeyboardInterrupt):
            return False

    async def gate(
        self,
        skill_name: str,
        action: str,
        params: dict | None,
        func: Callable[..., Coroutine[Any, Any, Any]],
        trace_id: str = "",
    ) -> dict:
        """Full gate: classify, approve, optionally dry-run, then execute and audit."""
        risk = self.classify_action(skill_name, action)

        approved = await self.request_approval(skill_name, action, params, trace_id)
        if not approved:
            return {"error": f"Action {skill_name}.{action} was denied (risk={risk.value})."}

        if self.dry_run:
            logger.info("[DRY-RUN] Would execute: %s.%s(%s)", skill_name, action, params)
            await self.audit_log.log(AuditEntry(
                actor="llm",
                action=action,
                skill=skill_name,
                params_summary=_redact(params),
                risk_level=risk.value,
                approved_by="auto",
                result_status="dry_run",
                trace_id=trace_id,
            ))
            return {"status": "dry_run", "action": f"{skill_name}.{action}", "params": params}

        import time
        start = time.time()
        try:
            result = await func()
            elapsed = (time.time() - start) * 1000
            await self.audit_log.log(AuditEntry(
                actor="llm",
                action=action,
                skill=skill_name,
                params_summary=_redact(params),
                risk_level=risk.value,
                approved_by="auto" if risk in self.auto_approve else "user",
                result_status="ok" if "error" not in result else "error",
                changes_summary=result.get("message", ""),
                duration_ms=elapsed,
                trace_id=trace_id,
            ))
            return result
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            await self.audit_log.log(AuditEntry(
                actor="llm",
                action=action,
                skill=skill_name,
                params_summary=_redact(params),
                risk_level=risk.value,
                approved_by="auto" if risk in self.auto_approve else "user",
                result_status="error",
                changes_summary=str(e),
                duration_ms=elapsed,
                trace_id=trace_id,
            ))
            raise


# ---------------------------------------------------------------------------
# LLM-based auto-approve classifier (adapted from Claude Code's auto mode)
# ---------------------------------------------------------------------------

_CLASSIFIER_PROMPT = """\
You are a safety classifier for a personal AI assistant called Jarvis.
Given a tool call, classify its risk as one of: ALLOW, ASK, DENY.

Rules:
- ALLOW: read-only queries, status checks, safe home automation (lights on/off during normal hours), playing music, listing items
- ASK: sending messages (WhatsApp/email), booking appointments, modifying schedules, deleting data, financial actions, anything visible to others
- DENY: obviously harmful actions, mass operations without confirmation, actions that could cause physical damage

Tool: {skill}.{action}
Parameters: {params}
Current time: {time}

Reply with EXACTLY one word: ALLOW, ASK, or DENY.
"""


class LLMClassifier:
    """Optional LLM-based risk classifier for auto-approve mode.

    When enabled, evaluates each tool call with a fast LLM query before execution.
    Falls back to the static RiskLevel classification if the LLM is unavailable.
    """

    def __init__(self, provider_factory=None):
        self._provider_factory = provider_factory
        self._enabled = False

    def enable(self, provider_factory=None):
        if provider_factory:
            self._provider_factory = provider_factory
        self._enabled = True

    def disable(self):
        self._enabled = False

    @property
    def is_enabled(self) -> bool:
        return self._enabled and self._provider_factory is not None

    async def classify(
        self, skill_name: str, action: str, params: dict | None = None
    ) -> str:
        """Returns 'ALLOW', 'ASK', or 'DENY'."""
        if not self.is_enabled:
            return "ASK"  # fallback to manual approval

        import time as _time
        from datetime import datetime

        prompt = _CLASSIFIER_PROMPT.format(
            skill=skill_name,
            action=action,
            params=_redact(params),
            time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

        try:
            provider = self._provider_factory()
            response = await provider.chat(
                messages=[
                    {"role": "system", "content": "Reply with exactly one word."},
                    {"role": "user", "content": prompt},
                ],
                tools=None,
            )
            # Normalize response
            from core.providers import LLMResponse
            if isinstance(response, LLMResponse):
                text = response.content
            else:
                text = getattr(getattr(response, "message", None), "content", "")
            verdict = text.strip().upper().split()[0] if text else "ASK"
            if verdict in ("ALLOW", "ASK", "DENY"):
                logger.info("LLM classifier: %s.%s → %s", skill_name, action, verdict)
                return verdict
            logger.warning("LLM classifier returned unexpected: %s, defaulting to ASK", verdict)
            return "ASK"
        except Exception as e:
            logger.warning("LLM classifier failed: %s, defaulting to ASK", e)
            return "ASK"


def _redact(params: dict | None) -> dict:
    """Remove sensitive values from params for logging."""
    if not params:
        return {}
    sensitive_keys = {"password", "secret", "token", "cookie", "session"}
    return {
        k: "***REDACTED***" if any(s in k.lower() for s in sensitive_keys) else v
        for k, v in params.items()
    }
