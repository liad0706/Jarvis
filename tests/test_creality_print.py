"""Tests for Creality Print skill."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skills.creality_print import CrealityPrintSkill


@pytest.fixture
def creality():
    with patch("skills.creality_print.get_settings") as mock_settings:
        settings = MagicMock()
        settings.creality_print_exe = "C:/fake/CrealityPrint.exe"
        mock_settings.return_value = settings
        yield CrealityPrintSkill()


class TestCrealityPrint:
    @pytest.mark.asyncio
    async def test_execute_unknown_action(self, creality):
        result = await creality.execute("nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_open_app_not_found(self, creality):
        result = await creality.do_open_app()
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_import_stl_file_not_found(self, creality):
        result = await creality.do_import_stl("/nonexistent/model.stl")
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_import_stl_valid_file(self, creality, tmp_path):
        stl_file = tmp_path / "test_model.stl"
        stl_file.write_text("solid test")

        # Mock pywinauto to avoid real UI interaction
        with patch("skills.creality_print.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.return_value = {
                "status": "imported",
                "file": str(stl_file),
                "message": f"Imported {stl_file.name}",
            }
            result = await creality.do_import_stl(str(stl_file))

        assert result["status"] == "imported"

    @pytest.mark.asyncio
    async def test_configure(self, creality):
        result = await creality.do_configure(
            layer_height="0.1", infill="50", supports="true"
        )
        assert result["status"] == "configured"
        assert result["settings"]["layer_height"] == "0.1"
        assert result["settings"]["infill"] == "50%"
        assert result["settings"]["supports"] is True

    @pytest.mark.asyncio
    async def test_configure_defaults(self, creality):
        result = await creality.do_configure()
        assert result["status"] == "configured"
        assert result["settings"]["layer_height"] == "0.2"
        assert result["settings"]["infill"] == "20%"
        assert result["settings"]["supports"] is False

    def test_get_actions(self, creality):
        actions = creality.get_actions()
        assert "open_app" in actions
        assert "import_stl" in actions
        assert "slice" in actions
        assert "start_print" in actions
        assert "configure" in actions

    def test_skill_name(self, creality):
        assert creality.name == "creality"
