"""Proactive engine — periodically checks if JARVIS should reach out to the user.

Now powered by EnvironmentAwareness: sees devices, music, time context,
and can suggest things like "I found a new device" or "it's late, want me
to turn off the lights?"

Guards:
- Won't send a second message if the user hasn't replied to the first.
- Global cooldown between any two proactive messages (default 15 min).
- Per-message dedup (same hash within 30 min).
"""

from __future__ import annotations

import json
import asyncio
import logging
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.environment_awareness import EnvironmentAwareness
    from core.memory_manager import MemoryManager

from config.settings import ollama_runtime_options

logger = logging.getLogger(__name__)

# Cooldown between ANY two proactive messages (seconds)
GLOBAL_COOLDOWN = 900  # 15 minutes
# Dedup window for identical messages
DEDUP_WINDOW = 1800    # 30 minutes
# Dedup window for the same proactive reason, even with slightly different wording
REASON_DEDUP_WINDOW = 21600  # 6 hours
# Max unanswered proactive messages before stopping
MAX_UNANSWERED = 1
# Mark a proactive suggestion as ignored after this many minutes with no reply
PENDING_RESPONSE_MINUTES = 5


class ProactiveEngine:
    def __init__(
        self,
        memory_manager: MemoryManager,
        awareness: EnvironmentAwareness | None = None,
        tts=None,
        broadcast_func=None,
        notifications=None,
    ):
        self.memory = memory_manager
        self.awareness = awareness
        self.tts = tts
        self.broadcast = broadcast_func  # async func to push to dashboard
        self.notifications = notifications
        self._last_triggered: dict[int, float] = {}
        self._last_reason_triggered: dict[str, float] = {}
        self._last_any_send: float = 0.0
        self._unanswered_count: int = 0
        self._pending_feedback_action_id: str | None = None
        self._pending_feedback_sent_at: float = 0.0

    # ------------------------------------------------------------------
    # Called by the orchestrator when the user sends ANY message
    # ------------------------------------------------------------------
    def user_responded(self):
        """Reset the unanswered counter — the user spoke."""
        self._unanswered_count = 0
        feedback_loop = getattr(self.awareness, "feedback_loop", None) if self.awareness else None
        if feedback_loop and self._pending_feedback_action_id:
            try:
                feedback_loop.record_reaction(
                    self._pending_feedback_action_id,
                    "positive",
                    "user_replied",
                )
            except ValueError:
                pass
        self._pending_feedback_action_id = None
        self._pending_feedback_sent_at = 0.0

    def _expire_pending_feedback_if_needed(self) -> None:
        """Mark old unanswered proactive suggestions as ignored."""
        if not self._pending_feedback_action_id:
            return
        if time.time() - self._pending_feedback_sent_at < PENDING_RESPONSE_MINUTES * 60:
            return
        feedback_loop = getattr(self.awareness, "feedback_loop", None) if self.awareness else None
        if feedback_loop:
            try:
                feedback_loop.infer_suggestion_feedback(
                    self._pending_feedback_action_id,
                    responded_within_minutes=PENDING_RESPONSE_MINUTES,
                )
            except ValueError:
                pass
        self._pending_feedback_action_id = None
        self._pending_feedback_sent_at = 0.0

    def _recent_proactive_entries(self, limit: int = 20) -> list[dict]:
        """Return recent proactive suggestion feedback entries, newest first."""
        feedback_loop = getattr(self.awareness, "feedback_loop", None) if self.awareness else None
        if not feedback_loop:
            return []
        entries = feedback_loop.get_all(limit=limit * 3)
        proactive = [entry for entry in entries if entry.get("action_type") == "proactive_suggestion"]
        return proactive[:limit]

    @staticmethod
    def _parse_proactive_detail(action_detail: str) -> tuple[str, str, str]:
        if not action_detail:
            return "", "", ""
        parts = action_detail.split("|", 2)
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        if len(parts) == 2:
            return parts[0], parts[1], ""
        return "", "", action_detail

    def _recent_proactive_summary(self, limit: int = 5) -> str:
        """Small prompt block so the model avoids repeating recent suggestions."""
        entries = self._recent_proactive_entries(limit=limit)
        if not entries:
            return ""
        lines = ["=== הצעות יזומות אחרונות ==="]
        for entry in entries[:limit]:
            reason, details, message = self._parse_proactive_detail(entry.get("action_detail", ""))
            reaction = entry.get("reaction") or "pending"
            summary = message or (f"{reason}: {details}" if details else reason or "unknown")
            lines.append(f"• {summary} [{reaction}]")
        return "\n".join(lines)

    def _should_suppress_reason(self, reason: str, details: str) -> bool:
        """Avoid sending the same proactive reason over and over again."""
        now = time.time()
        last_local = self._last_reason_triggered.get(reason, 0.0)
        if now - last_local < REASON_DEDUP_WINDOW:
            logger.debug("[ProactiveEngine] suppressing repeated reason %s (local cooldown)", reason)
            return True

        cutoff = datetime.fromtimestamp(now - REASON_DEDUP_WINDOW)
        for entry in self._recent_proactive_entries(limit=30):
            try:
                entry_ts = datetime.fromisoformat(entry["timestamp"])
            except asyncio.CancelledError:
                raise
            except Exception:
                continue
            if entry_ts < cutoff:
                continue
            prev_reason, prev_details, _ = self._parse_proactive_detail(entry.get("action_detail", ""))
            if prev_reason != reason:
                continue
            reaction = entry.get("reaction")
            if reaction in {None, "ignored", "negative", "neutral"}:
                if not details or not prev_details or prev_details == details:
                    logger.debug("[ProactiveEngine] suppressing repeated reason %s from feedback history", reason)
                    return True
        return False

    # ------------------------------------------------------------------
    # Main check loop (called every ~5 min from main.py)
    # ------------------------------------------------------------------
    async def check(self):
        try:
            self._expire_pending_feedback_if_needed()
            # Guard: don't pile up messages if user isn't responding
            if self._unanswered_count >= MAX_UNANSWERED:
                logger.debug("[ProactiveEngine] skipping — %d unanswered message(s)", self._unanswered_count)
                return

            # Guard: global cooldown
            now = time.time()
            if now - self._last_any_send < GLOBAL_COOLDOWN:
                remaining = int(GLOBAL_COOLDOWN - (now - self._last_any_send))
                logger.debug("[ProactiveEngine] skipping — global cooldown (%ds left)", remaining)
                return

            context = await self._build_context()

            # Use a structured approach: LLM picks a TEMPLATE NUMBER,
            # then we fill in the details — avoids garbled Hebrew output.
            prompt = (
                "You are JARVIS, a smart home assistant for User.\n"
                "Look at the current environment state and decide if you should notify the user.\n\n"
                f"ENVIRONMENT:\n{context}\n\n"
                "RULES:\n"
                "- Only notify if there is REAL value. If nothing special — return should_notify: false.\n"
                "- Do NOT send trivial messages like 'everything is fine' or 'are you happy?'\n"
                "- Avoid repeating the same proactive reason if it was already sent recently.\n"
                "- If the best suggestion would repeat a recent one, return should_notify: false.\n\n"
                "VALID REASONS to notify:\n"
                "1. NEW_DEVICE — a new unknown device was found on the network\n"
                "2. LIGHTS_LATE — it's after 23:00 and lights are still on\n"
                "3. REMINDER — a calendar event or reminder is approaching\n"
                "4. SHABBAT — Shabbat is approaching, suggest turning off devices\n"
                "5. DEVICE_ON_LONG — a device has been on for a very long time\n"
                "6. PATTERN — user usually does something at this time\n"
                "7. OTHER — something else worth mentioning\n\n"
                "Return ONLY valid JSON:\n"
                '{"should_notify": false}\n'
                "OR:\n"
                '{"should_notify": true, "reason_code": "LIGHTS_LATE", '
                '"details": "bedroom light on since 20:00", "priority": "medium"}'
            )

            kw: dict[str, Any] = {
                "model": self.memory.settings.ollama_model,
                "messages": [{"role": "user", "content": prompt}],
            }
            oopts = ollama_runtime_options(self.memory.settings)
            if oopts:
                kw["options"] = oopts
            response = await self.memory._client.chat(**kw)
            raw = response.message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            # Try to extract JSON from potentially messy output
            json_match = re.search(r'\{[^{}]*\}', raw)
            if not json_match:
                logger.debug("[ProactiveEngine] no JSON found in response")
                return
            result = json.loads(json_match.group())

            if not result.get("should_notify"):
                return
            if result.get("priority") == "low":
                return

            # Convert reason_code + details into a clean Hebrew message
            reason = result.get("reason_code", "OTHER")
            details = self._sanitise_details(result.get("details", ""))
            if self._should_suppress_reason(reason, details):
                return
            message = self._build_hebrew_message(reason, details)

            if message:
                await self._deliver({
                    "message": message,
                    "reason": reason,
                    "details": details,
                    "priority": result.get("priority", "medium"),
                })

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("[ProactiveEngine] error: %s", e)

    # ------------------------------------------------------------------
    # Build clean Hebrew messages from templates (avoids garbled LLM output)
    # ------------------------------------------------------------------
    _TEMPLATES = {
        "NEW_DEVICE": "גיליתי מכשיר חדש ברשת: {details}. רוצה שאוסיף אותו?",
        "LIGHTS_LATE": "השעה כבר מאוחרת והאורות עדיין דולקים. לכבות?",
        "REMINDER": "תזכורת: {details}",
        "SHABBAT": "שבת נכנסת בקרוב. רוצה שאכבה מכשירים?",
        "DEVICE_ON_LONG": "שמתי לב ש{details} דולק כבר הרבה זמן. לכבות?",
        "PATTERN": "בדרך כלל בשעה הזו אתה {details}. רוצה שאעשה את זה?",
    }

    def _build_hebrew_message(self, reason_code: str, details: str) -> str:
        """Build a clean Hebrew message from a template + details.

        We NEVER pass free-form LLM text to the user.  Only validated
        details are inserted into known templates.  "OTHER" is always
        rejected — the local model cannot produce reliable free-form Hebrew.
        """
        template = self._TEMPLATES.get(reason_code)
        if template:
            if reason_code in {"NEW_DEVICE", "REMINDER", "DEVICE_ON_LONG", "PATTERN"} and not details:
                return ""
            # If template needs {details} but we have nothing clean, use generic
            if "{details}" in template and not details:
                # Return the template with a safe fallback
                return template.replace("{details}", "משהו")
            try:
                return template.format(details=details or "משהו")
            except (KeyError, IndexError):
                return template.replace("{details}", details or "משהו")

        # "OTHER" — never trust free-form LLM output
        return ""

    @staticmethod
    def _sanitise_details(text: str) -> str:
        """Clean LLM details field — return empty string if garbled."""
        if not text or len(text) < 2:
            return ""
        # Cap length — details should be short
        if len(text) > 80:
            text = text[:80]
        # Reject mixed Hebrew+Latin in same word (e.g. "תorgeous")
        if re.search(r'[\u0590-\u05FF][a-zA-Z]|[a-zA-Z][\u0590-\u05FF]', text):
            return ""
        # Reject if it has too many non-Hebrew/non-basic chars
        heb_or_basic = sum(
            1 for c in text
            if '\u0590' <= c <= '\u05FF'  # Hebrew
            or c.isascii() and (c.isalpha() or c.isdigit() or c in ' .,!?:;-')
        )
        if heb_or_basic < len(text) * 0.7:
            return ""
        # Reject long "words" (>15 chars) — garbled text sign
        for word in text.split():
            if len(word) > 15:
                return ""
        # Reject if no Hebrew at all (details should be in Hebrew)
        if not re.search(r'[\u0590-\u05FF]', text):
            return ""
        return text.strip()

    async def _deliver(self, result: dict[str, Any]) -> None:
        """Deliver a proactive notification — dedup, then push via TTS + dashboard."""
        msg = result.get("message", "")
        if not msg:
            return

        msg_hash = hash(msg)
        now = time.time()

        # Per-message dedup
        last = self._last_triggered.get(msg_hash, 0)
        if now - last < DEDUP_WINDOW:
            return

        self._last_triggered[msg_hash] = now
        if result.get("reason"):
            self._last_reason_triggered[result["reason"]] = now
        self._last_any_send = now
        self._unanswered_count += 1

        logger.info("[ProactiveEngine] %s (reason: %s, unanswered: %d)",
                     msg, result.get("reason", "?"), self._unanswered_count)

        feedback_loop = getattr(self.awareness, "feedback_loop", None) if self.awareness else None
        if feedback_loop:
            detail_blob = "|".join([
                result.get("reason", ""),
                result.get("details", ""),
                msg,
            ])
            self._pending_feedback_action_id = feedback_loop.record_action(
                "proactive_suggestion",
                detail_blob,
            )
            self._pending_feedback_sent_at = now

        action_journal = getattr(self.awareness, "action_journal", None) if self.awareness else None
        if action_journal:
            try:
                action_journal.record(
                    action_type="proactive",
                    action_name=result.get("reason", "proactive"),
                    params_summary=result.get("details", ""),
                    result_summary=msg,
                    success=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("Action journal proactive record failed: %s", e)

        # Push to dashboard as a chat message
        if self.broadcast:
            try:
                await self.broadcast({
                    "type": "chat.assistant",
                    "content": f"💡 {msg}",
                    "proactive": True,
                })
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("broadcast failed: %s", e)

        # Speak via TTS if available
        if self.tts:
            try:
                await self.tts.speak(msg)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("TTS failed: %s", e)

        if self.notifications:
            try:
                await self.notifications.notify(
                    title="Jarvis Suggestion",
                    message=msg,
                    source="proactive",
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("Notification push failed: %s", e)

    async def _build_context(self) -> str:
        """Build rich context from memory + environment awareness."""
        parts = []

        # Environment state
        if self.awareness:
            try:
                snap = await self.awareness.snapshot(include_discoveries=True)
                env_text = self.awareness.format_for_prompt(snap)
                parts.append(env_text)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("awareness snapshot failed: %s", e)

        # Intentions and facts from memory
        try:
            intentions = await self.memory.memory.get_episodic_memories(
                memory_type="intention", limit=5,
            )
            facts = await self.memory.memory.get_all_facts()

            intentions_text = "; ".join(m["content"] for m in intentions) if intentions else "אין"
            facts_text = ", ".join(f"{k}={v}" for k, v in facts.items()) if facts else "אין"
            parts.append(f"כוונות פתוחות: {intentions_text}")
            parts.append(f"עובדות ידועות: {facts_text}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("memory context failed: %s", e)

        # Fallback: at least include time
        if not parts:
            parts.append(f"שעה: {datetime.now().hour}:00")

        recent_proactive = self._recent_proactive_summary(limit=5)
        if recent_proactive:
            parts.append(recent_proactive)

        return "\n\n".join(parts)
