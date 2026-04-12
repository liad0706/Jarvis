"""Spotify control skill using spotipy."""

import asyncio
import logging
import os

from core.skill_base import BaseSkill
from config import get_settings

logger = logging.getLogger(__name__)


class SpotifySkill(BaseSkill):
    name = "spotify"
    description = (
        "Control Spotify — play songs/playlists/albums/artists, pause, skip, search, "
        "list devices. Use play_playlist for playlists by name, play for single songs, "
        "play_album for albums, play_artist for artist radio. "
        "All play actions auto-wait up to 30s for a device (e.g. Apple TV booting)."
    )

    def __init__(self):
        self.settings = get_settings()
        self._sp = None

    def _get_client(self):
        """Lazy-init Spotify client."""
        if self._sp:
            return self._sp

        if not self.settings.spotipy_client_id:
            raise RuntimeError(
                "Spotify not configured. Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET in .env"
            )

        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        # Set env vars for spotipy
        os.environ["SPOTIPY_CLIENT_ID"] = self.settings.spotipy_client_id
        os.environ["SPOTIPY_CLIENT_SECRET"] = self.settings.spotipy_client_secret
        os.environ["SPOTIPY_REDIRECT_URI"] = self.settings.spotipy_redirect_uri

        scope = (
            "user-modify-playback-state user-read-playback-state "
            "user-read-currently-playing playlist-modify-public "
            "playlist-modify-private playlist-read-private"
        )
        auth = SpotifyOAuth(scope=scope, cache_path=".spotify_cache")
        self._sp = spotipy.Spotify(auth_manager=auth)
        return self._sp

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except RuntimeError as e:
            return {"error": str(e)}

    def _get_devices(self, sp) -> list[dict]:
        """Get available Spotify Connect devices."""
        try:
            result = sp.devices()
            return result.get("devices", [])
        except Exception:
            return []

    def _find_active_device(self, sp) -> dict | None:
        """Find an active device, preferring the one already active."""
        devices = self._get_devices(sp)
        if not devices:
            return None
        # Prefer the currently active device
        for d in devices:
            if d.get("is_active"):
                return d
        # Otherwise return the first available
        return devices[0]

    async def _wait_for_device(self, sp, max_wait: int = 30, interval: float = 3.0) -> dict | None:
        """Wait up to max_wait seconds for a Spotify device to become available.
        Useful when Apple TV / speaker is still booting up."""
        import time
        deadline = time.time() + max_wait
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            device = await asyncio.to_thread(self._find_active_device, sp)
            if device:
                logger.info("Spotify device found after %d attempt(s): %s", attempt, device.get("name"))
                return device
            logger.debug("No Spotify device yet (attempt %d), waiting %.0fs...", attempt, interval)
            await asyncio.sleep(interval)
        return None

    async def do_devices(self) -> dict:
        """List available Spotify Connect devices."""
        sp = self._get_client()
        devices = await asyncio.to_thread(self._get_devices, sp)
        if not devices:
            return {"status": "no_devices", "devices": [],
                    "reply_to_user_hebrew": "אין מכשירי Spotify פעילים. פתח את Spotify באחד המכשירים."}
        lines = []
        for d in devices:
            active = " ✅" if d.get("is_active") else ""
            lines.append(f"• {d['name']} ({d['type']}){active}")
        return {
            "status": "ok",
            "devices": devices,
            "reply_to_user_hebrew": "מכשירי Spotify:\n" + "\n".join(lines),
        }

    async def _ensure_device(self, sp, device_id: str = "") -> tuple[str | None, dict | None]:
        """Find or wait for a Spotify device. Returns (device_id, device_dict) or (None, None)."""
        if device_id:
            return device_id, None
        device = await asyncio.to_thread(self._find_active_device, sp)
        if not device:
            logger.info("No active Spotify device — waiting for one to appear...")
            device = await self._wait_for_device(sp, max_wait=30, interval=3.0)
        if device:
            return device["id"], device
        return None, None

    async def do_play(self, query: str = "", device_id: str = "", **_kwargs) -> dict:
        """Play a song by name, or resume playback if no query given.
        If no active device is found, waits up to 30 seconds for one to appear
        (e.g. when Apple TV is still booting)."""
        sp = self._get_client()

        if not query and not device_id:
            def _resume():
                sp.start_playback()
                return {
                    "status": "resumed",
                    "reply_to_user_hebrew": "׳”׳׳©׳›׳×׳™ ׳׳ ׳’׳",
                }

            return await asyncio.to_thread(_resume)

        target_id, device = await self._ensure_device(sp, device_id)
        if not target_id:
            return {
                "error": "No active Spotify device found after waiting 30s",
                "reply_to_user_hebrew": (
                    "לא מצאתי מכשיר Spotify פעיל. "
                    "תפתח את Spotify בממיר/טלפון/מחשב ונסה שוב."
                ),
            }

        def _play():
            if query:
                results = sp.search(q=query, type="track", limit=1)
                tracks = results["tracks"]["items"]
                if not tracks:
                    return {"error": f"No song found for '{query}'"}
                track = tracks[0]
                sp.start_playback(uris=[track["uri"]], device_id=target_id)
                return {
                    "status": "playing",
                    "track": track["name"],
                    "artist": ", ".join(a["name"] for a in track["artists"]),
                    "device": device.get("name", "?") if device else "?",
                    "reply_to_user_hebrew": f"מנגן: {track['name']} — {', '.join(a['name'] for a in track['artists'])}",
                }
            else:
                sp.start_playback(device_id=target_id)
                return {"status": "resumed",
                        "reply_to_user_hebrew": "המשכתי לנגן"}

        return await asyncio.to_thread(_play)

    async def do_play_playlist(self, name: str = "", shuffle: bool = True, device_id: str = "") -> dict:
        """Play a playlist by name. Searches the user's playlists first, then public.
        Use this for requests like 'play my playlist X' or 'shuffle playlist Y'."""
        sp = self._get_client()

        if not name:
            return {"error": "חסר שם פלייליסט",
                    "reply_to_user_hebrew": "לא אמרת איזה פלייליסט לנגן."}

        target_id, device = await self._ensure_device(sp, device_id)
        if not target_id:
            return {
                "error": "No active Spotify device found after waiting 30s",
                "reply_to_user_hebrew": (
                    "לא מצאתי מכשיר Spotify פעיל. "
                    "תפתח את Spotify בממיר/טלפון/מחשב ונסה שוב."
                ),
            }

        def _play_playlist():
            name_lower = name.lower().strip()

            # 1) Search user's own playlists first
            playlist_uri = None
            playlist_name = None
            offset = 0
            while offset < 200:
                batch = sp.current_user_playlists(limit=50, offset=offset)
                items = batch.get("items") or []
                if not items:
                    break
                for pl in items:
                    if pl["name"].lower().strip() == name_lower:
                        playlist_uri = pl["uri"]
                        playlist_name = pl["name"]
                        break
                if playlist_uri:
                    break
                offset += 50

            # 2) Fuzzy match — partial name match
            if not playlist_uri:
                offset = 0
                while offset < 200:
                    batch = sp.current_user_playlists(limit=50, offset=offset)
                    items = batch.get("items") or []
                    if not items:
                        break
                    for pl in items:
                        if name_lower in pl["name"].lower():
                            playlist_uri = pl["uri"]
                            playlist_name = pl["name"]
                            break
                    if playlist_uri:
                        break
                    offset += 50

            # 3) Public search fallback
            if not playlist_uri:
                results = sp.search(q=name, type="playlist", limit=5)
                playlists = results.get("playlists", {}).get("items", [])
                if playlists:
                    # Prefer exact match
                    for pl in playlists:
                        if pl["name"].lower().strip() == name_lower:
                            playlist_uri = pl["uri"]
                            playlist_name = pl["name"]
                            break
                    if not playlist_uri:
                        playlist_uri = playlists[0]["uri"]
                        playlist_name = playlists[0]["name"]

            if not playlist_uri:
                return {
                    "error": f"Playlist '{name}' not found",
                    "reply_to_user_hebrew": f"לא מצאתי פלייליסט בשם '{name}'.",
                }

            # Set shuffle before starting
            try:
                sp.shuffle(shuffle, device_id=target_id)
            except Exception:
                pass  # Some devices don't support shuffle toggle

            sp.start_playback(context_uri=playlist_uri, device_id=target_id)

            shuffle_text = " (ערבוב)" if shuffle else ""
            return {
                "status": "playing",
                "playlist": playlist_name,
                "uri": playlist_uri,
                "shuffle": shuffle,
                "device": device.get("name", "?") if device else "?",
                "reply_to_user_hebrew": f"מנגן פלייליסט: {playlist_name}{shuffle_text} 🎵",
            }

        return await asyncio.to_thread(_play_playlist)

    async def do_play_album(self, query: str = "", device_id: str = "") -> dict:
        """Play an album by name/artist."""
        sp = self._get_client()

        if not query:
            return {"error": "חסר שם אלבום"}

        target_id, device = await self._ensure_device(sp, device_id)
        if not target_id:
            return {
                "error": "No active Spotify device found",
                "reply_to_user_hebrew": "לא מצאתי מכשיר Spotify פעיל.",
            }

        def _play_album():
            results = sp.search(q=query, type="album", limit=1)
            albums = results.get("albums", {}).get("items", [])
            if not albums:
                return {"error": f"No album found for '{query}'",
                        "reply_to_user_hebrew": f"לא מצאתי אלבום בשם '{query}'."}
            album = albums[0]
            sp.start_playback(context_uri=album["uri"], device_id=target_id)
            return {
                "status": "playing",
                "album": album["name"],
                "artist": ", ".join(a["name"] for a in album["artists"]),
                "device": device.get("name", "?") if device else "?",
                "reply_to_user_hebrew": f"מנגן אלבום: {album['name']} — {', '.join(a['name'] for a in album['artists'])} 🎵",
            }

        return await asyncio.to_thread(_play_album)

    async def do_play_artist(self, query: str = "", device_id: str = "") -> dict:
        """Play an artist's top tracks / radio."""
        sp = self._get_client()

        if not query:
            return {"error": "חסר שם אמן"}

        target_id, device = await self._ensure_device(sp, device_id)
        if not target_id:
            return {
                "error": "No active Spotify device found",
                "reply_to_user_hebrew": "לא מצאתי מכשיר Spotify פעיל.",
            }

        def _play_artist():
            results = sp.search(q=query, type="artist", limit=1)
            artists = results.get("artists", {}).get("items", [])
            if not artists:
                return {"error": f"No artist found for '{query}'",
                        "reply_to_user_hebrew": f"לא מצאתי אמן בשם '{query}'."}
            artist = artists[0]
            sp.start_playback(context_uri=artist["uri"], device_id=target_id)
            return {
                "status": "playing",
                "artist": artist["name"],
                "device": device.get("name", "?") if device else "?",
                "reply_to_user_hebrew": f"מנגן: {artist['name']} 🎵",
            }

        return await asyncio.to_thread(_play_artist)

    async def do_pause(self) -> dict:
        """Pause the current playback."""
        sp = self._get_client()
        try:
            await asyncio.to_thread(sp.pause_playback)
        except Exception as e:
            msg = str(e)
            if "403" in msg or "Restriction violated" in msg:
                return {"status": "paused", "message": "Playback already stopped or device unavailable"}
            raise
        return {"status": "paused", "message": "Playback paused"}

    async def do_skip(self) -> dict:
        """Skip to the next track."""
        sp = self._get_client()
        await asyncio.to_thread(sp.next_track)
        return {"status": "skipped", "message": "Skipped to next track"}

    async def do_previous(self) -> dict:
        """Go to the previous track."""
        sp = self._get_client()
        await asyncio.to_thread(sp.previous_track)
        return {"status": "previous", "message": "Went to previous track"}

    async def do_current(self) -> dict:
        """Get the currently playing track."""
        sp = self._get_client()

        def _current():
            cur = sp.current_playback()
            if not cur or not cur.get("item"):
                return {"status": "idle", "message": "Nothing is currently playing"}
            track = cur["item"]
            return {
                "status": "playing" if cur["is_playing"] else "paused",
                "track": track["name"],
                "artist": ", ".join(a["name"] for a in track["artists"]),
                "album": track["album"]["name"],
                "progress": f"{cur['progress_ms'] // 60000}:{(cur['progress_ms'] % 60000) // 1000:02d}",
                "duration": f"{track['duration_ms'] // 60000}:{(track['duration_ms'] % 60000) // 1000:02d}",
            }

        return await asyncio.to_thread(_current)

    async def do_search(self, query: str) -> dict:
        """Search for songs on Spotify."""
        sp = self._get_client()

        def _search():
            results = sp.search(q=query, type="track", limit=5)
            tracks = results["tracks"]["items"]
            return {
                "status": "ok",
                "results": [
                    {
                        "name": t["name"],
                        "artist": ", ".join(a["name"] for a in t["artists"]),
                        "uri": t["uri"],
                    }
                    for t in tracks
                ],
                "count": len(tracks),
                "message": f"Found {len(tracks)} tracks for '{query}'",
            }

        return await asyncio.to_thread(_search)

    async def do_add_to_playlist(self, playlist_name: str, track_query: str = "") -> dict:
        """Add a track to a playlist. If no track_query, adds current track."""
        sp = self._get_client()

        def _add():
            # Find playlist
            playlists = sp.current_user_playlists(limit=50)
            target = None
            for pl in playlists["items"]:
                if pl["name"].lower() == playlist_name.lower():
                    target = pl
                    break

            if not target:
                return {"error": f"Playlist '{playlist_name}' not found"}

            # Get track URI
            if track_query:
                results = sp.search(q=track_query, type="track", limit=1)
                tracks = results["tracks"]["items"]
                if not tracks:
                    return {"error": f"Track not found: {track_query}"}
                track_uri = tracks[0]["uri"]
                track_name = tracks[0]["name"]
            else:
                cur = sp.current_playback()
                if not cur or not cur.get("item"):
                    return {"error": "No track playing and no query provided"}
                track_uri = cur["item"]["uri"]
                track_name = cur["item"]["name"]

            sp.playlist_add_items(target["id"], [track_uri])
            return {
                "status": "added",
                "track": track_name,
                "playlist": playlist_name,
                "message": f"Added '{track_name}' to '{playlist_name}'",
            }

        return await asyncio.to_thread(_add)

    async def do_create_playlist(self, name: str, description: str = "") -> dict:
        """Create a new Spotify playlist."""
        sp = self._get_client()

        def _create():
            user_id = sp.current_user()["id"]
            pl = sp.user_playlist_create(user_id, name, public=True, description=description)
            return {
                "status": "created",
                "playlist": pl["name"],
                "id": pl["id"],
                "message": f"Created playlist '{name}'",
            }

        return await asyncio.to_thread(_create)

    async def do_get_playlists(self) -> dict:
        """List the user's Spotify playlists."""
        sp = self._get_client()

        def _list():
            playlists = sp.current_user_playlists(limit=20)
            return {
                "status": "ok",
                "playlists": [
                    {"name": p["name"], "tracks": p["tracks"]["total"]}
                    for p in playlists["items"]
                ],
                "count": len(playlists["items"]),
            }

        return await asyncio.to_thread(_list)

    async def do_set_volume(self, volume: str = "50") -> dict:
        """Set the playback volume (0-100)."""
        sp = self._get_client()
        vol = max(0, min(100, int(volume)))
        await asyncio.to_thread(sp.volume, vol)
        return {"status": "ok", "volume": vol, "message": f"Volume set to {vol}%"}
