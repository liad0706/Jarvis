"""Tests for the appointment booker skill."""

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from skills.appointment_booker import AppointmentBookerSkill


@pytest.fixture
def booker():
    with patch("skills.appointment_booker.get_settings") as mock_settings:
        settings = MagicMock()
        settings.kamarlek_barber_name = "ישי פרץ"
        mock_settings.return_value = settings
        yield AppointmentBookerSkill()


class TestAppointmentBooker:
    @pytest.mark.asyncio
    async def test_execute_unknown_action(self, booker):
        result = await booker.execute("nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_book_appointment_no_params(self, booker):
        result = await booker.do_book_appointment()
        assert result["status"] == "need_info"
        assert "specify" in result["message"].lower() or "check_availability" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_book_appointment_missing_time(self, booker):
        result = await booker.do_book_appointment(date="2026-04-01")
        assert result["status"] == "need_info"

    @pytest.mark.asyncio
    async def test_book_appointment_missing_date(self, booker):
        result = await booker.do_book_appointment(time_slot="10:00")
        assert result["status"] == "need_info"

    def test_barber_name_from_settings(self, booker):
        assert booker.barber_name == "ישי פרץ"

    def test_get_actions(self, booker):
        actions = booker.get_actions()
        assert "check_availability" in actions
        assert "book_appointment" in actions
        assert "search_barber" in actions

    def test_skill_name(self, booker):
        assert booker.name == "appointment"

    @pytest.mark.asyncio
    async def test_check_availability_playwright_error(self, booker):
        """Test graceful failure when Playwright is not available."""
        with patch.object(booker, "_get_browser", side_effect=Exception("Playwright not installed")):
            result = await booker.do_check_availability()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_search_barber_default_name(self, booker):
        """Test that search_barber uses default barber name."""
        with patch.object(booker, "_get_browser", side_effect=Exception("No browser")):
            result = await booker.do_search_barber()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_search_barber_custom_name(self, booker):
        """Test search with custom barber name."""
        with patch.object(booker, "_get_browser", side_effect=Exception("No browser")):
            result = await booker.do_search_barber(name="John Doe")
        assert "error" in result
