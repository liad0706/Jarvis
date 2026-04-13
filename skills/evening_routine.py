"""Evening routine — User's automated wind-down sequence.

Steps (executed in order):
1. Turn off all lights via Home Assistant
2. Pause / stop Spotify if playing
3. Turn off Apple TV
4. Turn off LG TV if on
5. Send a "good night" summary: what happened today + tomorrow's weather
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import httpx

from config import get_settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPOTIFY_CACHE = PROJECT_ROOT / ".spotify_cache"
APPLE_TV_IP = "[APPLE_TV_IP]"
APPLE_TV_CREDS = PROJECT_ROOT / "data" / "apple_tv.conf"


async def _step1_lights_off() -> dict:
    """Turn off all lights via Home Assistant."""
    s = get_settings()
    ha_url = s.ha_url.rstrip("/")
    ha_token = s.ha_token

    if not ha_token:
        return {"step": 1, "status": "error", "detail": "HA token not configured"}

    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Fetch all light entities
            r = await client.get(f"{ha_url}/api/states", headers=headers)
            r.raise_for_status()
            states = r.json()

        lights = [s["entity_id"] for s in states if s["entity_id"].startswith("light.")]

        if not lights:
            return {"step": 1, "status": "ok", "detail": "לא נמצאו אורות לכיבוי"}

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{ha_url}/api/services/light/turn_off",
                headers=headers,
                json={"entity_id": lights},
            )
            r.raise_for_status()

        return {"step": 1, "status": "ok", "detail": f"כובו {len(lights)} אורות"}
    except Exception as e:
        return {"step": 1, "status": "error", "detail": str(e)}


async def _step2_pause_spotify() -> dict:
    """Pause Spotify if something is playing."""
    s = get_settings()

    if not s.spotipy_client_id:
        return {"step": 2, "status": "error", "detail": "Spotify not configured"}

    os.environ["SPOTIPY_CLIENT_ID"] = s.spotipy_client_id
    os.environ["SPOTIPY_CLIENT_SECRET"] = s.spotipy_client_secret
    os.environ["SPOTIPY_REDIRECT_URI"] = s.spotipy_redirect_uri

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        return {"step": 2, "status": "error", "detail": "spotipy not installed"}

    def _do_pause():
        scope = "user-modify-playback-state user-read-playback-state"
        auth = SpotifyOAuth(scope=scope, cache_path=str(SPOTIFY_CACHE))
        sp = spotipy.Spotify(auth_manager=auth)

        current = sp.current_playback()
        if not current or not current.get("is_playing"):
            return {"step": 2, "status": "ok", "detail": "Spotify כבר עצור"}

        sp.pause_playback()
        return {"step": 2, "status": "ok", "detail": "Spotify הושהה"}

    try:
        return await asyncio.to_thread(_do_pause)
    except Exception as e:
        return {"step": 2, "status": "error", "detail": str(e)}


async def _step3_apple_tv_off() -> dict:
    """Turn off the Apple TV."""
    try:
        import pyatv
        from pyatv.storage.file_storage import FileStorage
    except ImportError:
        return {"step": 3, "status": "error", "detail": "pyatv not installed"}

    loop = asyncio.get_running_loop()
    atv = None
    try:
        cred_path = APPLE_TV_CREDS
        cred_path.parent.mkdir(parents=True, exist_ok=True)
        storage = FileStorage(cred_path.as_posix(), loop)
        await storage.load()

        atvs = await pyatv.scan(loop, hosts=[APPLE_TV_IP], storage=storage, timeout=8)
        if not atvs:
            return {"step": 3, "status": "error", "detail": f"Apple TV not found at {APPLE_TV_IP}"}

        conf = atvs[0]
        atv = await pyatv.connect(conf, loop, storage=storage)

        try:
            await atv.power.turn_off(await_new_state=False)
        except Exception as e:
            logger.warning("power.turn_off failed, trying suspend: %s", e)
            try:
                await atv.remote_control.suspend()
            except Exception as e2:
                logger.warning("suspend also failed: %s", e2)
                return {"step": 3, "status": "error", "detail": f"Apple TV כיבוי נכשל: {e2}"}

        await storage.save()
        return {"step": 3, "status": "ok", "detail": "Apple TV כובה"}

    except Exception as e:
        return {"step": 3, "status": "error", "detail": str(e)}
    finally:
        if atv:
            try:
                pending = atv.close()
                if pending:
                    await asyncio.gather(*pending)
            except Exception:
                pass


async def _step4_lg_tv_off() -> dict:
    """Turn off the LG TV if it is on."""
    try:
        from skills.dynamic.lg_tv import LgTvSkill
    except ImportError:
        return {"step": 4, "status": "error", "detail": "LG TV skill not available"}

    tv = LgTvSkill()
    try:
        result = await tv.execute("power_off", {})
        if "error" in result:
            return {"step": 4, "status": "error", "detail": result["error"]}
        return {"step": 4, "status": "ok", "detail": "LG TV כובה"}
    except Exception as e:
        msg = str(e)
        # TV might already be off
        if "not connected" in msg.lower() or "connection" in msg.lower():
            return {"step": 4, "status": "ok", "detail": "LG TV כבר כבוי או לא מחובר"}
        return {"step": 4, "status": "error", "detail": msg}


async def _step5_goodnight_summary() -> dict:
    """Build a Hebrew 'good night' summary: today's activity + tomorrow's weather."""
    lines = ["לילה טוב! 🌙"]

    # --- What happened today (action journal) ---
    try:
        from core.action_journal import ActionJournal
        journal = ActionJournal()
        today_entries = journal.get_today()
        if today_entries:
            action_names = []
            for entry in today_entries[-10:]:  # last 10 actions
                skill = entry.get("skill", "")
                action = entry.get("action", "")
                if skill and action:
                    action_names.append(f"{skill}.{action}")
            if action_names:
                unique_actions = list(dict.fromkeys(action_names))  # deduplicate, preserve order
                lines.append(f"היום ביצענו: {', '.join(unique_actions[:6])}")
    except Exception as e:
        logger.debug("action journal summary failed: %s", e)

    # --- Tomorrow's weather ---
    try:
        from skills.weather_skill import WeatherSkill
        weather = WeatherSkill()
        forecast = await weather.do_tomorrow()
        reply = forecast.get("reply_to_user_hebrew")
        if reply:
            lines.append(reply)
    except Exception as e:
        logger.debug("weather forecast failed: %s", e)

    summary = "\n".join(lines)
    return {"step": 5, "status": "ok", "detail": summary}


async def evening_routine() -> dict:
    """Execute the full evening routine. Returns a summary of all steps."""
    from datetime import datetime

    now = datetime.now()
    weekday = now.weekday()  # 0=Mon … 5=Sat 6=Sun
    is_shabbat = (weekday == 5) or (weekday == 4 and now.hour >= 16)
    if is_shabbat:
        logger.info("Evening routine skipped — Shabbat")
        return {
            "routine": "evening",
            "succeeded": 0, "failed": 0, "total": 0,
            "summary": "שבת שלום! לא מפעיל שגרת ערב בשבת.",
            "steps": [],
        }

    results = []

    logger.info("=== Evening Routine: Step 1 — Lights Off ===")
    r1 = await _step1_lights_off()
    results.append(r1)
    logger.info("Step 1 result: %s", r1)

    logger.info("=== Evening Routine: Step 2 — Pause Spotify ===")
    r2 = await _step2_pause_spotify()
    results.append(r2)
    logger.info("Step 2 result: %s", r2)

    logger.info("=== Evening Routine: Step 3 — Apple TV Off ===")
    r3 = await _step3_apple_tv_off()
    results.append(r3)
    logger.info("Step 3 result: %s", r3)

    logger.info("=== Evening Routine: Step 4 — LG TV Off ===")
    r4 = await _step4_lg_tv_off()
    results.append(r4)
    logger.info("Step 4 result: %s", r4)

    logger.info("=== Evening Routine: Step 5 — Good Night Summary ===")
    r5 = await _step5_goodnight_summary()
    results.append(r5)
    logger.info("Step 5 result: %s", r5)

    succeeded = sum(1 for r in results if r["status"] == "ok")
    failed = sum(1 for r in results if r["status"] == "error")

    summary_parts = []
    for r in results:
        icon = "+" if r["status"] == "ok" else "X"
        summary_parts.append(f"{icon} שלב {r['step']}: {r['detail']}")

    summary = "\n".join(summary_parts)
    logger.info("Evening routine done: %d/%d succeeded", succeeded, len(results))

    return {
        "routine": "evening",
        "succeeded": succeeded,
        "failed": failed,
        "total": len(results),
        "summary": summary,
        "steps": results,
    }
