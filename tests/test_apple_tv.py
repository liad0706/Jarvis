"""Apple TV skill — unit tests (no real device required)."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_apple_tv_discover_requires_host():
    from skills.apple_tv import AppleTVSkill

    s = AppleTVSkill()
    s.settings = type(
        "S",
        (),
        {"apple_tv_host": "", "apple_tv_credentials_file": "/tmp/jarvis_apple_tv_test.conf"},
    )()
    r = await s.do_discover()
    assert "error" in r


@pytest.mark.asyncio
async def test_apple_tv_pair_unknown_protocol():
    from skills.apple_tv import AppleTVSkill

    s = AppleTVSkill()
    s.settings = type(
        "S",
        (),
        {"apple_tv_host": "10.0.0.1", "apple_tv_credentials_file": "/tmp/jarvis_apple_tv_test.conf"},
    )()
    r = await s.do_pair_protocol(protocol="invalid_proto")
    assert "error" in r


@pytest.mark.asyncio
async def test_bootstrap_registers_apple_tv():
    from core.bootstrap import bootstrap

    ctx = await bootstrap()
    names = [x.name for x in ctx.registry.all_skills()]
    assert "apple_tv" in names
