"""Tests for the permission gate and risk classification."""

import pytest
from unittest.mock import AsyncMock, patch

from core.audit import AuditLog
from core.permissions import PermissionGate, RiskLevel


@pytest.fixture
async def audit_log(tmp_path):
    log = AuditLog(db_path=tmp_path / "test_audit.db")
    await log.init()
    yield log
    await log.close()


@pytest.fixture
def gate(audit_log):
    return PermissionGate(audit_log=audit_log)


@pytest.mark.asyncio
class TestRiskLevel:
    async def test_ordering(self):
        assert RiskLevel.READ < RiskLevel.WRITE  # __ge__ not __lt__, but test logically
        assert RiskLevel.CRITICAL >= RiskLevel.EXTERNAL
        assert RiskLevel.WRITE >= RiskLevel.READ
        assert not (RiskLevel.READ > RiskLevel.WRITE)


@pytest.mark.asyncio
class TestPermissionGate:
    async def test_classify_known_action(self, gate):
        assert gate.classify_action("code", "run") == RiskLevel.CRITICAL
        assert gate.classify_action("code", "list") == RiskLevel.READ
        assert gate.classify_action("spotify", "play") == RiskLevel.EXTERNAL

    async def test_classify_unknown_defaults_to_write(self, gate):
        assert gate.classify_action("unknown_skill", "unknown_action") == RiskLevel.WRITE

    async def test_auto_approve_read_and_write(self, gate):
        approved = await gate.request_approval("code", "list", trace_id="t1")
        assert approved is True

        approved = await gate.request_approval("code", "write", trace_id="t2")
        assert approved is True

    async def test_external_auto_approved_by_default(self, gate):
        approved = await gate.request_approval("spotify", "play", trace_id="t3")
        assert approved is True

    async def test_external_prompts_when_auto_off(self, audit_log):
        gate = PermissionGate(audit_log=audit_log, auto_approve_external=False)
        with patch("core.permissions.asyncio.to_thread", new_callable=AsyncMock, return_value="y"):
            approved = await gate.request_approval("spotify", "play", trace_id="t3b")
        assert approved is True

    async def test_external_denied_by_user(self, audit_log):
        gate = PermissionGate(audit_log=audit_log, auto_approve_external=False)
        with patch("core.permissions.asyncio.to_thread", new_callable=AsyncMock, return_value="n"):
            approved = await gate.request_approval("spotify", "play", trace_id="t4")
        assert approved is False

    async def test_safe_mode_blocks_external(self, audit_log):
        gate = PermissionGate(audit_log=audit_log, safe_mode=True)
        approved = await gate.request_approval("spotify", "play", trace_id="t5")
        assert approved is False

    async def test_safe_mode_blocks_critical(self, audit_log):
        gate = PermissionGate(audit_log=audit_log, safe_mode=True)
        approved = await gate.request_approval("code", "run", trace_id="t6")
        assert approved is False

    async def test_dry_run_skips_execution(self, audit_log):
        gate = PermissionGate(audit_log=audit_log, dry_run=True)
        called = False

        async def _func():
            nonlocal called
            called = True
            return {"status": "ok"}

        result = await gate.gate("code", "list", {}, _func, trace_id="t7")
        assert result["status"] == "dry_run"
        assert called is False
