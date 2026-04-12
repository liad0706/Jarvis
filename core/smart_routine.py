"""Smart Routine — scheduled tasks that go through the LLM for intelligent decisions.

Instead of hardcoded "play song X every morning", the LLM receives:
- The routine's intent (e.g., "wake User up with music and light")
- Current environment state (what's on, what time, what played recently)
- And decides what to actually do (pick a different song, adjust brightness, etc.)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.orchestrator import Orchestrator
    from core.environment_awareness import EnvironmentAwareness

logger = logging.getLogger(__name__)


class SmartRoutine:
    """A routine definition that the LLM executes with awareness."""

    def __init__(
        self,
        name: str,
        intent_he: str,
        guidelines: str,
        fallback_func=None,
    ):
        self.name = name
        self.intent_he = intent_he      # Hebrew description of what to achieve
        self.guidelines = guidelines     # Constraints and suggestions
        self.fallback_func = fallback_func  # Original hardcoded function as fallback

    def build_prompt(self, env_context: str, recent_songs: str = "") -> str:
        """Build the internal prompt that the orchestrator will process."""
        songs_section = ""
        if recent_songs:
            # Limit to last 5 songs to save tokens
            lines = recent_songs.strip().split("\n")[-5:]
            songs_section = f"\nשירים אחרונים (אל תחזור):\n" + "\n".join(lines) + "\n"

        # Keep env context compact — truncate if too long
        if len(env_context) > 600:
            env_context = env_context[:600] + "\n..."

        return (
            f"[שגרה: {self.name}]\n"
            f"מטרה: {self.intent_he}\n"
            f"{env_context}\n"
            f"{songs_section}\n"
            f"{self.guidelines}\n\n"
            "חשוב: השתמש רק בכלים (tool calls) שלך. "
            "אל תכתוב קוד, אל תקרא קבצים, אל תנסה HTTP ישירות. "
            "קרא לכלים: smart_home_turn_on, apple_tv_power_on, "
            "apple_tv_apps_open_app, lg_tv_set_volume, spotify_play_playlist. "
            "בצע עכשיו."
        )


class SmartRoutineRunner:
    """Executes smart routines through the orchestrator with environment awareness."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        awareness: EnvironmentAwareness,
    ):
        self.orchestrator = orchestrator
        self.awareness = awareness
        self._routines: dict[str, SmartRoutine] = {}

    def register(self, routine: SmartRoutine) -> None:
        self._routines[routine.name] = routine
        logger.info("SmartRoutineRunner: registered '%s'", routine.name)

    def _provider_supports_tools(self) -> bool:
        """Check if the current LLM provider can natively call Jarvis tools.

        CLI-based providers (codex-cli, claude-cli) run as sandboxed
        subprocesses that try to *execute* code instead of returning
        structured tool-call JSON.  They cannot reach local devices
        (Home Assistant, Apple TV, Spotify, LG TV) and will spin in
        circles reading files and attempting blocked HTTP calls.

        Providers with native tool calling (OpenAI API, Anthropic API,
        Ollama, CodexOAuth) work correctly.
        """
        from core.providers import CodexCLIProvider, ClaudeCLIProvider

        # The orchestrator lazily creates the provider via .provider property
        provider = self.orchestrator.provider
        return not isinstance(provider, (CodexCLIProvider, ClaudeCLIProvider))

    async def run(self, routine_name: str) -> dict:
        """Execute a smart routine through the LLM (or fallback if provider can't do tools)."""
        from datetime import datetime

        now = datetime.now()
        weekday = now.weekday()  # 0=Mon … 5=Sat 6=Sun
        is_shabbat = (weekday == 5) or (weekday == 4 and now.hour >= 16)
        if is_shabbat:
            logger.info("SmartRoutine '%s' skipped — Shabbat", routine_name)
            return {
                "routine": routine_name,
                "status": "skipped",
                "detail": "שבת שלום! לא מפעיל שגרה בשבת.",
            }

        routine = self._routines.get(routine_name)
        if not routine:
            return {"error": f"Smart routine '{routine_name}' not registered"}

        # ── CLI providers (codex-cli, claude-cli) can't call tools ──
        # They run in a sandbox and try to execute code instead of
        # returning tool-call JSON.  Skip the LLM and run fallback directly.
        if not self._provider_supports_tools():
            logger.info(
                "SmartRoutine '%s': provider is CLI-based (sandboxed) — "
                "skipping LLM, running fallback directly",
                routine_name,
            )
            if routine.fallback_func:
                try:
                    return await routine.fallback_func()
                except Exception as fb_err:
                    logger.exception("Fallback failed: %s", fb_err)
                    return {"routine": routine_name, "status": "error", "detail": str(fb_err)}
            return {"routine": routine_name, "status": "error", "detail": "No fallback defined"}

        # ── Provider supports native tool calling — use the LLM ──
        try:
            # Gather environment state — with a timeout so a stuck device
            # (e.g. Apple TV offline) doesn't block the whole routine.
            try:
                snap = await asyncio.wait_for(
                    self.awareness.snapshot(include_discoveries=False),
                    timeout=15,
                )
            except asyncio.TimeoutError:
                logger.warning("SmartRoutine '%s': env snapshot timed out, using minimal context", routine_name)
                snap = await self.awareness._get_time_context()
                snap = {"time": snap}
            env_context = self.awareness.format_for_prompt(snap)

            # Get recently played songs to avoid repetition
            recent_songs = ""
            if self.awareness.action_journal:
                try:
                    songs = self.awareness.action_journal.get_songs_played_recently(days=7)
                    if songs:
                        song_names = []
                        for s in songs[-5:]:
                            detail = s.get("params_summary", "") or s.get("result_summary", "")
                            if detail:
                                song_names.append(f"  • {detail}")
                        recent_songs = "\n".join(song_names)
                except Exception:
                    pass

            # Build the smart prompt
            prompt = routine.build_prompt(env_context, recent_songs)
            logger.info("SmartRoutine '%s': sending to LLM with env context", routine_name)

            # Save and temporarily replace the conversation to avoid polluting chat
            saved_conv = self.orchestrator.conversation
            self.orchestrator.conversation = []

            try:
                response = await self.orchestrator.handle(prompt)
            finally:
                # Restore the real conversation
                self.orchestrator.conversation = saved_conv

            logger.info("SmartRoutine '%s' completed: %s", routine_name, response[:200])
            return {
                "routine": routine_name,
                "status": "ok",
                "response": response,
                "env_snapshot": snap.get("time", {}),
            }

        except Exception as e:
            logger.exception("SmartRoutine '%s' failed, trying fallback", routine_name)
            # Fall back to hardcoded routine
            if routine.fallback_func:
                try:
                    return await routine.fallback_func()
                except Exception as fb_err:
                    logger.exception("Fallback also failed: %s", fb_err)
            return {"routine": routine_name, "status": "error", "detail": str(e)}


# ------------------------------------------------------------------
# Pre-built smart routines
# ------------------------------------------------------------------

MORNING_ROUTINE = SmartRoutine(
    name="morning_routine",
    intent_he="להעיר את המשתמש בצורה נעימה — להדליק אור, להפעיל Apple TV עם Spotify, לנגן שיר, ולשים ווליום 15 בטלוויזיה",
    guidelines="""\
- שלב 1: תדליק את האור (smart_home_turn_on — לבן, בהירות מלאה).
- שלב 2: תדליק את ה-Apple TV (apple_tv_power_on) ותפתח Spotify (apple_tv_apps_open_app עם com.spotify.client).
- שלב 3: תשים ווליום 15 בטלוויזיה (lg_tv_set_volume עם level=15).
- שלב 4: רק אחרי שה-Apple TV דלוק — תנגן מהפלייליסט ב-Spotify.
  הפקודה spotify_play מחכה אוטומטית עד 30 שניות למכשיר Spotify.
- לגבי מוזיקה: תמיד תנגן מהפלייליסט "Jarvis Mix - User" עם ערבוב (shuffle).
  תשתמש ב-spotify_play_playlist עם name="Jarvis Mix - User" ו-shuffle=true.
  ככה המשתמש תמיד ישמע שיר מוכר שהוא אוהב, אבל כל בוקר שיר אחר.
- אם הכל כבר דולק (האור והמוזיקה) — אל תעשה כלום, רק תדווח שהכל פועל.
- אם משהו נכשל — נסה גישה אחרת לפני שתוותר.""",
)

EVENING_ROUTINE = SmartRoutine(
    name="evening_routine",
    intent_he="להכין את הבית ללילה — לכבות את כל האורות, להפסיק Spotify, לכבות Apple TV ו-LG TV, ולשלוח סיכום לילה טוב",
    guidelines="""\
- שלב 1: כבה את כל האורות (smart_home_turn_off_all_lights).
- שלב 2: השהה את Spotify אם מנגן (spotify_pause).
- שלב 3: כבה את ה-Apple TV (apple_tv_power_off).
- שלב 4: כבה את ה-LG TV אם דולק (lg_tv_power_off).
- שלב 5: שלח סיכום לילה טוב — מה קרה היום + תחזית מזג אוויר למחר (weather_tomorrow).
- אם מכשיר כבר כבוי — אל תנסה לכבות שוב, רק תדווח שהוא כבר כבוי.
- אם משהו נכשל — המשך לשלבים הבאים בכל זאת.""",
)
