"""Morning routine — User's automated wake-up sequence.

Steps (executed in order):
1. Turn on room light (white, full brightness) via Home Assistant
2. Power on Apple TV + launch Spotify app
3. Play "כולם גנבים" by אושר כוהן on Spotify → Apple TV
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

LIGHT_ENTITY = "light.10_0_0_4"
APPLE_TV_IP = "[APPLE_TV_IP]"
APPLE_TV_CREDS = PROJECT_ROOT / "data" / "apple_tv.conf"
SPOTIFY_CACHE = PROJECT_ROOT / ".spotify_cache"
FALLBACK_DEVICE_ID = "1dbf55485f6d6d73ad183b8a0de759a15eb6778b"


async def _step1_light_on() -> dict:
    """Turn on white light at full brightness via Home Assistant."""
    s = get_settings()
    ha_url = s.ha_url.rstrip("/")
    ha_token = s.ha_token

    if not ha_token:
        return {"step": 1, "status": "error", "detail": "HA token not configured"}

    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "entity_id": LIGHT_ENTITY,
        "rgb_color": [255, 255, 255],
        "brightness": 255,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{ha_url}/api/services/light/turn_on",
                headers=headers,
                json=payload,
            )
            r.raise_for_status()
        return {"step": 1, "status": "ok", "detail": "אור לבן הודלק"}
    except Exception as e:
        return {"step": 1, "status": "error", "detail": str(e)}


async def _step2_apple_tv_spotify() -> dict:
    """Power on Apple TV, wait for boot, then launch Spotify app."""
    try:
        import pyatv
        from pyatv.storage.file_storage import FileStorage
    except ImportError:
        return {"step": 2, "status": "error", "detail": "pyatv not installed"}

    loop = asyncio.get_running_loop()
    MAX_RETRIES = 2

    for attempt in range(1, MAX_RETRIES + 1):
        atv = None
        try:
            cred_path = APPLE_TV_CREDS
            cred_path.parent.mkdir(parents=True, exist_ok=True)
            storage = FileStorage(cred_path.as_posix(), loop)
            await storage.load()

            # Shorter scan timeout — don't block the routine too long
            atvs = await pyatv.scan(loop, hosts=[APPLE_TV_IP], storage=storage, timeout=8)
            if not atvs:
                logger.warning("Apple TV scan attempt %d/%d — not found", attempt, MAX_RETRIES)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(3)
                    continue
                return {"step": 2, "status": "error", "detail": f"Apple TV not found at {APPLE_TV_IP}"}

            conf = atvs[0]
            atv = await pyatv.connect(conf, loop, storage=storage)

            try:
                await atv.power.turn_on(await_new_state=False)
            except Exception as e:
                logger.warning("power.turn_on failed, trying wakeup: %s", e)
                try:
                    await atv.remote_control.wakeup()
                except Exception as e2:
                    logger.warning("wakeup also failed: %s", e2)

            logger.info("Apple TV powering on — waiting 12s for boot")
            await asyncio.sleep(12)

            try:
                await atv.apps.launch_app("com.spotify.client")
                logger.info("Spotify app launched — waiting 8s for load")
                await asyncio.sleep(8)
            except Exception as e:
                logger.warning("Spotify launch failed (attempt %d): %s", attempt, e)
                if attempt < MAX_RETRIES:
                    continue
                return {"step": 2, "status": "partial", "detail": f"Apple TV on but Spotify launch failed: {e}"}

            await storage.save()
            return {"step": 2, "status": "ok", "detail": "Apple TV on + Spotify launched"}

        except Exception as e:
            logger.warning("Apple TV attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(3)
                continue
            return {"step": 2, "status": "error", "detail": str(e)}
        finally:
            if atv:
                try:
                    pending = atv.close()
                    if pending:
                        await asyncio.gather(*pending)
                except Exception:
                    pass


MORNING_PLAYLIST_NAME = "Jarvis Mix - User"


async def _step3_play_music() -> dict:
    """Play from 'Jarvis Mix-User' playlist with shuffle on Spotify → Apple TV."""
    s = get_settings()

    if not s.spotipy_client_id:
        return {"step": 3, "status": "error", "detail": "Spotify not configured"}

    os.environ["SPOTIPY_CLIENT_ID"] = s.spotipy_client_id
    os.environ["SPOTIPY_CLIENT_SECRET"] = s.spotipy_client_secret
    os.environ["SPOTIPY_REDIRECT_URI"] = s.spotipy_redirect_uri

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        return {"step": 3, "status": "error", "detail": "spotipy not installed"}

    def _do_play():
        scope = (
            "user-modify-playback-state user-read-playback-state "
            "user-read-currently-playing playlist-read-private"
        )
        auth = SpotifyOAuth(scope=scope, cache_path=str(SPOTIFY_CACHE))
        sp = spotipy.Spotify(auth_manager=auth)

        # --- Find target device (Apple TV > "yes" > fallback) ---
        devices = sp.devices().get("devices", [])
        target_id = None
        for d in devices:
            if d.get("name", "").lower() == "apple tv":
                target_id = d["id"]
                break

        if not target_id:
            for d in devices:
                if d.get("name", "").lower() == "yes":
                    target_id = d["id"]
                    break

        if not target_id:
            target_id = FALLBACK_DEVICE_ID

        try:
            sp.transfer_playback(target_id, force_play=False)
        except Exception as e:
            logger.warning("transfer_playback failed: %s", e)

        # --- Find the playlist by name ---
        playlist_uri = None
        playlists = sp.current_user_playlists(limit=50)
        while playlists:
            for pl in playlists.get("items", []):
                if pl.get("name", "").strip().lower() == MORNING_PLAYLIST_NAME.lower():
                    playlist_uri = pl["uri"]
                    break
            if playlist_uri or not playlists.get("next"):
                break
            playlists = sp.next(playlists)

        if not playlist_uri:
            return {"step": 3, "status": "error", "detail": f"Playlist '{MORNING_PLAYLIST_NAME}' not found"}

        # --- Enable shuffle, then play the playlist ---
        try:
            sp.shuffle(True, device_id=target_id)
        except Exception as e:
            logger.warning("shuffle toggle failed: %s", e)

        sp.start_playback(device_id=target_id, context_uri=playlist_uri)

        return {
            "step": 3,
            "status": "ok",
            "detail": f"Playing playlist '{MORNING_PLAYLIST_NAME}' with shuffle 🔀",
        }

    return await asyncio.to_thread(_do_play)


async def _step4_gradual_volume(start_vol: int = 5, end_vol: int = 20) -> dict:
    """Gradually raise LG TV volume — +1 every minute from start_vol to end_vol."""
    try:
        from skills.dynamic.lg_tv import LgTvSkill
    except ImportError:
        return {"step": 4, "status": "error", "detail": "LG TV skill not available"}

    tv = LgTvSkill()
    result = await tv.execute("gradual_volume", {
        "start": start_vol,
        "end": end_vol,
        "interval": 60,
    })

    if "error" in result:
        return {"step": 4, "status": "error", "detail": result["error"]}

    total_minutes = end_vol - start_vol
    return {
        "step": 4,
        "status": "ok",
        "detail": f"ווליום מתחיל ב-{start_vol}, עולה ל-{end_vol} במשך {total_minutes} דקות",
    }


async def morning_routine() -> dict:
    """Execute the full morning routine. Returns a summary of all steps."""
    from datetime import datetime
    now = datetime.now()
    weekday = now.weekday()  # 0=Mon … 5=Sat 6=Sun
    is_shabbat = (weekday == 5) or (weekday == 4 and now.hour >= 16)
    if is_shabbat:
        logger.info("Morning routine skipped — Shabbat")
        return {
            "routine": "morning",
            "succeeded": 0, "failed": 0, "total": 0,
            "summary": "שבת שלום! לא מפעיל שגרת בוקר בשבת.",
            "steps": [],
        }

    results = []

    # Step 1: Light — independent, always runs first
    logger.info("=== Morning Routine: Step 1 — Light ===")
    r1 = await _step1_light_on()
    results.append(r1)
    logger.info("Step 1 result: %s", r1)

    # Step 2: Apple TV + Spotify App — may fail, but don't block other steps
    logger.info("=== Morning Routine: Step 2 — Apple TV + Spotify App ===")
    r2 = await _step2_apple_tv_spotify()
    results.append(r2)
    logger.info("Step 2 result: %s", r2)

    # Step 3: Play Music — try even if Apple TV failed (Spotify may find device)
    logger.info("=== Morning Routine: Step 3 — Play Music ===")
    r3 = await _step3_play_music()
    results.append(r3)
    logger.info("Step 3 result: %s", r3)

    # Step 4: Gradual Volume — independent of Apple TV/Spotify
    logger.info("=== Morning Routine: Step 4 — Gradual Volume ===")
    r4 = await _step4_gradual_volume(start_vol=5, end_vol=20)
    results.append(r4)
    logger.info("Step 4 result: %s", r4)

    succeeded = sum(1 for r in results if r["status"] == "ok")
    failed = sum(1 for r in results if r["status"] == "error")

    summary_parts = []
    for r in results:
        icon = "+" if r["status"] == "ok" else "X"
        summary_parts.append(f"{icon} שלב {r['step']}: {r['detail']}")

    summary = "\n".join(summary_parts)
    logger.info("Morning routine done: %d/%d succeeded", succeeded, len(results))

    return {
        "routine": "morning",
        "succeeded": succeeded,
        "failed": failed,
        "total": len(results),
        "summary": summary,
        "steps": results,
    }
