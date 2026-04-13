"""Tests for the security policy engine."""

import pytest
from unittest.mock import patch

from core.policy import SecurityPolicy


@pytest.fixture
def policy():
    return SecurityPolicy()


class TestSecurityPolicy:
    def test_safe_imports_pass(self, policy):
        code = "import json\nimport re\nfrom datetime import datetime\n"
        violations = policy.validate_imports(code)
        assert violations == []

    def test_blocked_import_subprocess(self, policy):
        code = "import subprocess\n"
        violations = policy.validate_imports(code)
        assert any("subprocess" in v for v in violations)

    def test_blocked_import_ctypes(self, policy):
        code = "import ctypes\n"
        violations = policy.validate_imports(code)
        assert any("ctypes" in v for v in violations)

    def test_blocked_prefix_win32(self, policy):
        code = "import win32api\n"
        violations = policy.validate_imports(code)
        assert any("win32" in v for v in violations)

    def test_core_imports_allowed(self, policy):
        code = "from core.skill_base import BaseSkill\n"
        violations = policy.validate_imports(code)
        assert violations == []

    def test_dangerous_builtins_detected(self, policy):
        code = "x = eval('2+2')\n"
        violations = policy.validate_code_safety(code)
        assert any("eval" in v for v in violations)

    def test_exec_detected(self, policy):
        code = "exec('print(1)')\n"
        violations = policy.validate_code_safety(code)
        assert any("exec" in v for v in violations)

    def test_dunder_access_detected(self, policy):
        code = "x.__subclasses__()\n"
        violations = policy.validate_code_safety(code)
        assert any("__subclasses__" in v for v in violations)

    def test_full_validate_combines_checks(self, policy):
        code = "import subprocess\nx = eval('1')\n"
        violations = policy.full_validate(code)
        assert len(violations) >= 2

    def test_package_allowed_default(self, policy):
        assert policy.is_package_allowed("requests") is True
        assert policy.is_package_allowed("numpy") is True

    def test_package_blocked_in_production(self):
        with patch("core.policy.get_settings") as mock:
            settings = mock.return_value
            settings.production_mode = True
            settings.allowed_packages = "requests,numpy"
            p = SecurityPolicy()
            assert p.is_package_allowed("some_random_pkg") is False

    def test_command_blocked(self, policy):
        assert policy.is_command_allowed(["rm", "-rf", "/"]) is False
        assert policy.is_command_allowed(["powershell", "-c", "dir"]) is False

    def test_command_allowed(self, policy):
        assert policy.is_command_allowed(["python", "script.py"]) is True
