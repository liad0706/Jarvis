from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from skills.smart_home import SmartHomeSkill


def _build_skill() -> SmartHomeSkill:
    with patch("skills.smart_home.get_settings") as mock_settings:
        mock_settings.return_value = SimpleNamespace(
            ha_url="http://localhost:8123",
            ha_token="test-token",
        )
        return SmartHomeSkill()


@pytest.mark.asyncio
async def test_discover_devices_falls_back_to_lan_when_ha_is_down():
    skill = _build_skill()
    skill._ha_discover = AsyncMock(return_value={"error": "Cannot connect to Home Assistant"})
    skill._lan_discover = AsyncMock(
        return_value={
            "status": "ok",
            "source": "lan",
            "found": 1,
            "devices": [{"entity_id": "yeelight_10.0.0.50"}],
        }
    )

    result = await skill.do_discover_devices()

    assert result["source"] == "lan"
    assert result["fallback_reason"] == "Cannot connect to Home Assistant"


@pytest.mark.asyncio
async def test_list_devices_falls_back_to_lan_before_cache():
    skill = _build_skill()
    skill._ha_discover = AsyncMock(return_value={"error": "Cannot connect to Home Assistant"})
    skill._lan_discover = AsyncMock(
        return_value={
            "status": "ok",
            "source": "lan",
            "found": 1,
            "devices": [{"entity_id": "kasa_10.0.0.60"}],
        }
    )

    result = await skill.do_list_devices()

    assert result["source"] == "lan"
    assert result["fallback_reason"] == "Cannot connect to Home Assistant"


@pytest.mark.asyncio
async def test_ha_get_suppresses_immediate_retries_after_failure():
    skill = _build_skill()
    skill._mark_ha_unavailable("GET", "/api/states", RuntimeError("down"))

    with patch("skills.smart_home.httpx.AsyncClient") as client_cls:
        result = await skill._ha_get("/api/states")

    assert result is None
    client_cls.assert_not_called()
