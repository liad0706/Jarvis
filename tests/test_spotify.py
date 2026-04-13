"""Tests for the Spotify controller skill."""

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from skills.spotify_controller import SpotifySkill


@pytest.fixture
def spotify():
    with patch("skills.spotify_controller.get_settings") as mock_settings:
        settings = MagicMock()
        settings.spotipy_client_id = "fake_id"
        settings.spotipy_client_secret = "fake_secret"
        settings.spotipy_redirect_uri = "http://localhost:8888/callback"
        mock_settings.return_value = settings
        skill = SpotifySkill()
        yield skill


@pytest.fixture
def mock_sp():
    """Create a mock Spotify client."""
    sp = MagicMock()
    sp.search.return_value = {
        "tracks": {
            "items": [
                {
                    "name": "Bohemian Rhapsody",
                    "artists": [{"name": "Queen"}],
                    "uri": "spotify:track:123",
                    "album": {"name": "A Night at the Opera"},
                    "duration_ms": 354000,
                }
            ]
        }
    }
    sp.current_playback.return_value = {
        "is_playing": True,
        "progress_ms": 60000,
        "item": {
            "name": "Bohemian Rhapsody",
            "artists": [{"name": "Queen"}],
            "album": {"name": "A Night at the Opera"},
            "uri": "spotify:track:123",
            "duration_ms": 354000,
        },
    }
    sp.current_user_playlists.return_value = {
        "items": [
            {"name": "My Playlist", "id": "pl_123", "tracks": {"total": 10}},
            {"name": "Rock", "id": "pl_456", "tracks": {"total": 25}},
        ]
    }
    sp.current_user.return_value = {"id": "user123"}
    sp.user_playlist_create.return_value = {
        "name": "New Playlist",
        "id": "pl_new",
    }
    return sp


class TestSpotifySkill:
    @pytest.mark.asyncio
    async def test_execute_unknown_action(self, spotify):
        result = await spotify.execute("nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_not_configured(self):
        with patch("skills.spotify_controller.get_settings") as mock_settings:
            settings = MagicMock()
            settings.spotipy_client_id = ""
            settings.spotipy_client_secret = ""
            mock_settings.return_value = settings
            skill = SpotifySkill()

            result = await skill.execute("play", {"query": "test"})
            assert "error" in result
            assert "not configured" in result["error"].lower() or "Spotify" in result["error"]

    @pytest.mark.asyncio
    async def test_play_with_query(self, spotify, mock_sp):
        spotify._sp = mock_sp

        result = await spotify.do_play(query="Bohemian Rhapsody")
        assert result["status"] == "playing"
        assert result["track"] == "Bohemian Rhapsody"
        assert result["artist"] == "Queen"
        mock_sp.start_playback.assert_called_once()

    @pytest.mark.asyncio
    async def test_play_no_results(self, spotify, mock_sp):
        mock_sp.search.return_value = {"tracks": {"items": []}}
        spotify._sp = mock_sp

        result = await spotify.do_play(query="nonexistent song xyz")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_play_resume(self, spotify, mock_sp):
        spotify._sp = mock_sp

        result = await spotify.do_play()
        assert result["status"] == "resumed"
        mock_sp.start_playback.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_pause(self, spotify, mock_sp):
        spotify._sp = mock_sp

        result = await spotify.do_pause()
        assert result["status"] == "paused"
        mock_sp.pause_playback.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip(self, spotify, mock_sp):
        spotify._sp = mock_sp

        result = await spotify.do_skip()
        assert result["status"] == "skipped"
        mock_sp.next_track.assert_called_once()

    @pytest.mark.asyncio
    async def test_previous(self, spotify, mock_sp):
        spotify._sp = mock_sp

        result = await spotify.do_previous()
        assert result["status"] == "previous"
        mock_sp.previous_track.assert_called_once()

    @pytest.mark.asyncio
    async def test_current(self, spotify, mock_sp):
        spotify._sp = mock_sp

        result = await spotify.do_current()
        assert result["status"] == "playing"
        assert result["track"] == "Bohemian Rhapsody"
        assert result["artist"] == "Queen"

    @pytest.mark.asyncio
    async def test_current_nothing_playing(self, spotify, mock_sp):
        mock_sp.current_playback.return_value = None
        spotify._sp = mock_sp

        result = await spotify.do_current()
        assert result["status"] == "idle"

    @pytest.mark.asyncio
    async def test_search(self, spotify, mock_sp):
        spotify._sp = mock_sp

        result = await spotify.do_search(query="Queen")
        assert result["status"] == "ok"
        assert result["count"] == 1
        assert result["results"][0]["name"] == "Bohemian Rhapsody"

    @pytest.mark.asyncio
    async def test_get_playlists(self, spotify, mock_sp):
        spotify._sp = mock_sp

        result = await spotify.do_get_playlists()
        assert result["status"] == "ok"
        assert result["count"] == 2
        assert result["playlists"][0]["name"] == "My Playlist"

    @pytest.mark.asyncio
    async def test_create_playlist(self, spotify, mock_sp):
        spotify._sp = mock_sp

        result = await spotify.do_create_playlist(name="Test Playlist", description="A test")
        assert result["status"] == "created"
        assert result["playlist"] == "New Playlist"
        mock_sp.user_playlist_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_to_playlist(self, spotify, mock_sp):
        spotify._sp = mock_sp

        result = await spotify.do_add_to_playlist(
            playlist_name="My Playlist", track_query="Bohemian Rhapsody"
        )
        assert result["status"] == "added"
        mock_sp.playlist_add_items.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_to_playlist_not_found(self, spotify, mock_sp):
        spotify._sp = mock_sp

        result = await spotify.do_add_to_playlist(
            playlist_name="Nonexistent Playlist", track_query="test"
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_volume(self, spotify, mock_sp):
        spotify._sp = mock_sp

        result = await spotify.do_set_volume(volume="75")
        assert result["status"] == "ok"
        assert result["volume"] == 75
        mock_sp.volume.assert_called_once_with(75)

    @pytest.mark.asyncio
    async def test_set_volume_clamped(self, spotify, mock_sp):
        spotify._sp = mock_sp

        result = await spotify.do_set_volume(volume="150")
        assert result["volume"] == 100

        result = await spotify.do_set_volume(volume="-10")
        assert result["volume"] == 0

    def test_get_actions(self, spotify):
        actions = spotify.get_actions()
        expected = ["play", "pause", "skip", "previous", "current", "search",
                     "add_to_playlist", "create_playlist", "get_playlists", "set_volume"]
        for a in expected:
            assert a in actions

    def test_skill_name(self, spotify):
        assert spotify.name == "spotify"
