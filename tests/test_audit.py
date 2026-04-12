"""Tests for the audit log."""

import pytest

from core.audit import AuditEntry, AuditLog


@pytest.fixture
async def audit_log(tmp_path):
    log = AuditLog(db_path=tmp_path / "test_audit.db")
    await log.init()
    yield log
    await log.close()


@pytest.mark.asyncio
class TestAuditLog:
    async def test_log_and_query(self, audit_log):
        entry = AuditEntry(
            actor="llm",
            action="search",
            skill="models",
            risk_level="external",
            result_status="ok",
        )
        await audit_log.log(entry)

        rows = await audit_log.query(limit=10)
        assert len(rows) == 1
        assert rows[0]["actor"] == "llm"
        assert rows[0]["skill"] == "models"
        assert rows[0]["risk_level"] == "external"

    async def test_query_by_skill(self, audit_log):
        await audit_log.log(AuditEntry(actor="llm", action="play", skill="spotify"))
        await audit_log.log(AuditEntry(actor="llm", action="search", skill="models"))

        rows = await audit_log.query(skill="spotify")
        assert len(rows) == 1
        assert rows[0]["skill"] == "spotify"

    async def test_query_by_risk(self, audit_log):
        await audit_log.log(AuditEntry(actor="llm", action="list", risk_level="read"))
        await audit_log.log(AuditEntry(actor="llm", action="run", risk_level="critical"))

        rows = await audit_log.query(risk_level="critical")
        assert len(rows) == 1
        assert rows[0]["action"] == "run"

    async def test_export_json(self, audit_log):
        await audit_log.log(AuditEntry(actor="user", action="test"))
        exported = await audit_log.export(fmt="json")
        assert '"actor": "user"' in exported

    async def test_export_text(self, audit_log):
        await audit_log.log(AuditEntry(actor="system", action="init", skill="core"))
        exported = await audit_log.export(fmt="text")
        assert "system" in exported
        assert "core" in exported
