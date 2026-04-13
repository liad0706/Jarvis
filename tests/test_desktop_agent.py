"""Tests for DesktopAgentSkill."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

from skills.dynamic.desktop_agent import DesktopAgentSkill


@pytest.fixture
def skill():
    return DesktopAgentSkill()


# ── capture ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_capture(skill, tmp_path, monkeypatch):
    import skills.dynamic.desktop_agent as mod
    monkeypatch.setattr(mod, "SCREENSHOT_DIR", tmp_path)

    fake_img = MagicMock()
    fake_pag = MagicMock()
    fake_pag.screenshot = MagicMock(return_value=fake_img)
    monkeypatch.setattr(skill, "_pag", lambda: fake_pag)

    result = await skill.execute("capture", {})
    assert result["status"] == "ok"
    assert "screenshot_path" in result
    fake_pag.screenshot.assert_called_once()
    fake_img.save.assert_called_once()


# ── capture_region ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_capture_region(skill, tmp_path, monkeypatch):
    import skills.dynamic.desktop_agent as mod
    monkeypatch.setattr(mod, "SCREENSHOT_DIR", tmp_path)

    fake_img = MagicMock()
    fake_pag = MagicMock()
    fake_pag.screenshot = MagicMock(return_value=fake_img)
    monkeypatch.setattr(skill, "_pag", lambda: fake_pag)

    result = await skill.execute("capture_region", {"x": 10, "y": 20, "width": 100, "height": 50})
    assert result["status"] == "ok"
    assert result["region"]["width"] == 100


@pytest.mark.asyncio
async def test_capture_region_bad_size(skill):
    result = await skill.execute("capture_region", {"x": 0, "y": 0, "width": -1, "height": 10})
    assert "error" in result


# ── click ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_click(skill, monkeypatch):
    fake_pag = MagicMock()
    monkeypatch.setattr(skill, "_pag", lambda: fake_pag)

    result = await skill.execute("click", {"x": 100, "y": 200})
    assert result["status"] == "ok"
    fake_pag.click.assert_called_once_with(100, 200, clicks=1, button="left")


@pytest.mark.asyncio
async def test_click_bad_button(skill, monkeypatch):
    fake_pag = MagicMock()
    monkeypatch.setattr(skill, "_pag", lambda: fake_pag)

    result = await skill.execute("click", {"x": 0, "y": 0, "button": "banana"})
    assert "error" in result


# ── move_mouse ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_move_mouse(skill, monkeypatch):
    fake_pag = MagicMock()
    monkeypatch.setattr(skill, "_pag", lambda: fake_pag)

    result = await skill.execute("move_mouse", {"x": 50, "y": 60})
    assert result["status"] == "ok"
    fake_pag.moveTo.assert_called_once()


# ── type_text ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_type_text(skill, monkeypatch):
    fake_pag = MagicMock()
    monkeypatch.setattr(skill, "_pag", lambda: fake_pag)

    result = await skill.execute("type_text", {"text": "hello"})
    assert result["status"] == "ok"
    assert result["chars_typed"] == 5


# ── press_key ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_press_key(skill, monkeypatch):
    fake_pag = MagicMock()
    monkeypatch.setattr(skill, "_pag", lambda: fake_pag)

    result = await skill.execute("press_key", {"key": "enter"})
    assert result["status"] == "ok"
    fake_pag.press.assert_called_once_with("enter", presses=1)


# ── hotkey ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hotkey(skill, monkeypatch):
    fake_pag = MagicMock()
    monkeypatch.setattr(skill, "_pag", lambda: fake_pag)

    result = await skill.execute("hotkey", {"keys": "ctrl+c"})
    assert result["status"] == "ok"
    fake_pag.hotkey.assert_called_once_with("ctrl", "c")


@pytest.mark.asyncio
async def test_hotkey_empty(skill):
    result = await skill.execute("hotkey", {"keys": ""})
    assert "error" in result


# ── scroll ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scroll(skill, monkeypatch):
    fake_pag = MagicMock()
    monkeypatch.setattr(skill, "_pag", lambda: fake_pag)

    result = await skill.execute("scroll", {"amount": -3})
    assert result["status"] == "ok"
    fake_pag.scroll.assert_called_once()


# ── active_window ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_active_window(skill, monkeypatch):
    fake_win = MagicMock()
    fake_win.title = "Notepad"
    fake_win.left = 0
    fake_win.top = 0
    fake_win.width = 800
    fake_win.height = 600

    fake_gw = MagicMock()
    fake_gw.getActiveWindow = MagicMock(return_value=fake_win)
    monkeypatch.setattr(skill, "_pgw", lambda: fake_gw)

    result = await skill.execute("active_window", {})
    assert result["status"] == "ok"
    assert result["window"]["title"] == "Notepad"


@pytest.mark.asyncio
async def test_active_window_none(skill, monkeypatch):
    fake_gw = MagicMock()
    fake_gw.getActiveWindow = MagicMock(return_value=None)
    monkeypatch.setattr(skill, "_pgw", lambda: fake_gw)

    result = await skill.execute("active_window", {})
    assert result["status"] == "ok"
    assert result["window"] is None


# ── mouse_position ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mouse_position(skill, monkeypatch):
    fake_pag = MagicMock()
    fake_pag.position = MagicMock(return_value=(123, 456))
    monkeypatch.setattr(skill, "_pag", lambda: fake_pag)

    result = await skill.execute("mouse_position", {})
    assert result["x"] == 123
    assert result["y"] == 456


# ── wait_for_window ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wait_for_window_found(skill, monkeypatch):
    fake_win = MagicMock()
    fake_win.title = "Chrome"

    fake_gw = MagicMock()
    fake_gw.getWindowsWithTitle = MagicMock(return_value=[fake_win])
    monkeypatch.setattr(skill, "_pgw", lambda: fake_gw)

    result = await skill.execute("wait_for_window", {"title": "Chrome", "timeout": 1})
    assert result["found"] is True


@pytest.mark.asyncio
async def test_wait_for_window_not_found(skill, monkeypatch):
    fake_gw = MagicMock()
    fake_gw.getWindowsWithTitle = MagicMock(return_value=[])
    fake_gw.getAllWindows = MagicMock(return_value=[])
    monkeypatch.setattr(skill, "_pgw", lambda: fake_gw)

    result = await skill.execute("wait_for_window", {"title": "NonExistent", "timeout": 1})
    assert result["found"] is False


# ── unknown action ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_action(skill):
    result = await skill.execute("bogus", {})
    assert "error" in result
