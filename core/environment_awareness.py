"""Environment Awareness Layer — gathers live state of Jarvis's world.

Collects smart-home devices, music playback, Apple TV status, time context,
network presence, calendar, action history, learned patterns, and feedback
into a single snapshot that the LLM can reason about.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.action_journal import ActionJournal
    from core.pattern_learner import PatternLearner
    from core.feedback_loop import FeedbackLoop
    from core.network_presence import NetworkPresence
    from core.calendar_awareness import CalendarAwareness

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEVICES_CACHE = PROJECT_ROOT / "data" / "smart_devices.json"


class EnvironmentAwareness:
    """Gathers a snapshot of everything Jarvis can see right now."""

    def __init__(self, registry=None, memory_manager=None):
        self._registry = registry
        self._memory = memory_manager

        # Pluggable sub-systems (set after init by bootstrap)
        self.action_journal: ActionJournal | None = None
        self.pattern_learner: PatternLearner | None = None
        self.feedback_loop: FeedbackLoop | None = None
        self.network_presence: NetworkPresence | None = None
        self.calendar: CalendarAwareness | None = None

    # ------------------------------------------------------------------
    # Individual collectors — each returns a dict/str, never raises
    # ------------------------------------------------------------------

    async def _get_smart_home_state(self) -> dict[str, Any]:
        """Current state of smart-home devices from cache + optional live refresh."""
        try:
            skill = self._registry.get("smart_home") if self._registry else None
            if skill:
                result = await skill.execute("list_devices", {})
                if result.get("devices"):
                    return {"devices": result["devices"], "source": "live"}

            if DEVICES_CACHE.exists():
                data = json.loads(DEVICES_CACHE.read_text(encoding="utf-8"))
                devices = data if isinstance(data, list) else data.get("devices", [])
                return {"devices": devices, "source": "cache"}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("smart_home state failed: %s", e)
        return {"devices": [], "source": "unavailable"}

    async def _get_music_state(self) -> dict[str, Any]:
        """Current Spotify playback."""
        try:
            skill = self._registry.get("spotify") if self._registry else None
            if skill:
                result = await skill.execute("current", {})
                if result.get("status") != "error":
                    return result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("music state failed: %s", e)
        return {"status": "unknown"}

    _atv_backoff_until: float = 0.0  # timestamp — skip status until this time
    _ATV_BACKOFF_SECS: float = 30 * 60  # 30 minutes after a failure

    async def _get_apple_tv_state(self) -> dict[str, Any]:
        """Apple TV power/media state (with backoff on failure)."""
        import time

        now_ts = time.monotonic()
        if now_ts < self._atv_backoff_until:
            return {"status": "unknown", "backoff": True}

        try:
            skill = self._registry.get("apple_tv") if self._registry else None
            if skill:
                result = await skill.execute("status", {})
                if result.get("device_name"):
                    # Success — reset backoff
                    EnvironmentAwareness._atv_backoff_until = 0.0
                    return result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("apple_tv state failed: %s", e)

        # Failed or no device — back off to avoid spamming logs
        EnvironmentAwareness._atv_backoff_until = now_ts + self._ATV_BACKOFF_SECS
        logger.info("Apple TV unreachable — skipping status checks for 30 min")
        return {"status": "unknown"}

    async def _get_time_context(self) -> dict[str, Any]:
        """Time-of-day context for smart decisions."""
        now = datetime.now()
        hour = now.hour
        if 5 <= hour < 12:
            period = "morning"
        elif 12 <= hour < 17:
            period = "afternoon"
        elif 17 <= hour < 21:
            period = "evening"
        else:
            period = "night"

        day_names_he = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
        weekday = now.weekday()
        is_shabbat = (weekday == 4 and hour >= 16) or (weekday == 5 and hour < 20)

        return {
            "time": now.strftime("%H:%M"),
            "date": now.strftime("%Y-%m-%d"),
            "day": day_names_he[weekday],
            "period": period,
            "is_shabbat": is_shabbat,
        }

    async def _get_new_discoveries(self) -> list[str]:
        """Check if there are devices/capabilities Jarvis hasn't told the user about."""
        discoveries = []
        try:
            skill = self._registry.get("smart_home") if self._registry else None
            if skill:
                result = await skill.execute("discover_devices", {})
                devices = result.get("devices", [])
                known_ids = set()
                if DEVICES_CACHE.exists():
                    cached = json.loads(DEVICES_CACHE.read_text(encoding="utf-8"))
                    cached_list = cached if isinstance(cached, list) else cached.get("devices", [])
                    known_ids = {d.get("entity_id") for d in cached_list}

                for d in devices:
                    if d.get("entity_id") and d["entity_id"] not in known_ids:
                        discoveries.append(
                            f"מכשיר חדש: {d.get('name', d['entity_id'])} ({d.get('type', 'unknown')})"
                        )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("discovery check failed: %s", e)
        return discoveries

    async def _get_presence(self) -> dict[str, Any]:
        """Who is home right now."""
        if not self.network_presence:
            return {}
        try:
            return await self.network_presence.scan()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("network presence failed: %s", e)
            return {}

    async def _get_ruview_sensing(self) -> dict[str, Any]:
        """WiFi-based human sensing from RuView (presence, vitals, pose)."""
        try:
            skill = self._registry.get("ruview") if self._registry else None
            if skill and hasattr(skill, "get_sensing_snapshot"):
                return await skill.get_sensing_snapshot()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("ruview sensing failed: %s", e)
        return {"status": "unavailable"}

    async def _get_family_presence(self) -> dict[str, Any]:
        """Unified family presence from all tracking sources."""
        try:
            skill = self._registry.get("presence_tracker") if self._registry else None
            if skill and hasattr(skill, "get_presence_snapshot"):
                return await skill.get_presence_snapshot()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("family presence failed: %s", e)
        return {"status": "unavailable"}

    # ------------------------------------------------------------------
    # Full snapshot
    # ------------------------------------------------------------------

    async def snapshot(self, include_discoveries: bool = False) -> dict[str, Any]:
        """Gather full environment snapshot."""
        import asyncio

        tasks = {
            "time": self._get_time_context(),
            "smart_home": self._get_smart_home_state(),
            "music": self._get_music_state(),
            "apple_tv": self._get_apple_tv_state(),
            "presence": self._get_presence(),
            "ruview": self._get_ruview_sensing(),
            "family": self._get_family_presence(),
        }
        if include_discoveries:
            tasks["discoveries"] = self._get_new_discoveries()

        results = {}
        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for key, result in zip(tasks.keys(), gathered):
            if isinstance(result, Exception):
                logger.debug("snapshot %s failed: %s", key, result)
                results[key] = {}
            else:
                results[key] = result

        return results

    def format_for_prompt(self, snap: dict[str, Any]) -> str:
        """Format a snapshot + all sub-systems into concise Hebrew text for the system prompt."""
        lines = ["=== מצב סביבה נוכחי ==="]

        # Time
        t = snap.get("time", {})
        if t:
            shabbat = " (שבת)" if t.get("is_shabbat") else ""
            lines.append(f"🕐 {t.get('time', '?')} | יום {t.get('day', '?')} | {t.get('period', '?')}{shabbat}")

        # Smart home
        sh = snap.get("smart_home", {})
        devices = sh.get("devices", [])
        if devices:
            on_devices = [d for d in devices if d.get("state") == "on"]
            off_devices = [d for d in devices if d.get("state") == "off"]
            if on_devices:
                names = ", ".join(d.get("name", d.get("entity_id", "?")) for d in on_devices[:5])
                lines.append(f"💡 דולקים: {names}")
            if off_devices:
                names = ", ".join(d.get("name", d.get("entity_id", "?")) for d in off_devices[:5])
                lines.append(f"🔌 כבויים: {names}")
        else:
            lines.append("💡 בית חכם: לא זמין")

        # Music
        m = snap.get("music", {})
        if m.get("status") == "playing":
            lines.append(f"🎵 מנגן: {m.get('track', '?')} — {m.get('artist', '?')} ({m.get('progress', '?')}/{m.get('duration', '?')})")
        elif m.get("status") == "paused":
            lines.append(f"🎵 מושהה: {m.get('track', '?')} — {m.get('artist', '?')}")
        else:
            lines.append("🎵 מוזיקה: לא פעילה")

        # Apple TV
        atv = snap.get("apple_tv", {})
        if atv.get("device_state"):
            lines.append(f"📺 Apple TV: {atv.get('device_state', '?')}")
            if atv.get("title"):
                lines.append(f"   מציג: {atv['title']}")

        # Family presence (unified tracking)
        family = snap.get("family", {})
        if family.get("status") == "ok":
            try:
                skill = self._registry.get("presence_tracker") if self._registry else None
                if skill and hasattr(skill, "format_presence_for_prompt"):
                    text = skill.format_presence_for_prompt(family)
                    if text:
                        lines.append(text)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("family presence format failed: %s", e)

        # RuView WiFi sensing
        ruview = snap.get("ruview", {})
        if ruview.get("status") == "ok":
            try:
                skill = self._registry.get("ruview") if self._registry else None
                if skill and hasattr(skill, "format_sensing_for_prompt"):
                    lines.append(skill.format_sensing_for_prompt(ruview))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("ruview format failed: %s", e)

        # Network presence
        presence = snap.get("presence", {})
        if presence.get("home") or presence.get("away"):
            lines.append("")
            if self.network_presence:
                lines.append(self.network_presence.format_for_prompt(presence))

        # New discoveries
        discoveries = snap.get("discoveries", [])
        if discoveries:
            lines.append("\n🆕 גילויים חדשים:")
            for d in discoveries:
                lines.append(f"   • {d}")

        # --- Sub-system context blocks ---

        # Action journal
        if self.action_journal:
            try:
                journal_text = self.action_journal.format_for_prompt()
                if journal_text:
                    lines.append("")
                    lines.append(journal_text)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("action journal format failed: %s", e)

        # Calendar
        if self.calendar:
            try:
                cal_text = self.calendar.format_for_prompt()
                if cal_text:
                    lines.append("")
                    lines.append(cal_text)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("calendar format failed: %s", e)

        # Learned patterns
        if self.pattern_learner:
            try:
                patterns_text = self.pattern_learner.format_for_prompt()
                if patterns_text:
                    lines.append("")
                    lines.append(patterns_text)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("pattern learner format failed: %s", e)

        # Feedback
        if self.feedback_loop:
            try:
                feedback_text = self.feedback_loop.format_for_prompt()
                if feedback_text:
                    lines.append("")
                    lines.append(feedback_text)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("feedback loop format failed: %s", e)

        return "\n".join(lines)
