"""Security policy engine — import allowlists, package restrictions, command validation."""

from __future__ import annotations

import ast
import logging
import re
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)


ALLOWED_STDLIB = {
    "json", "re", "math", "datetime", "time", "collections", "itertools",
    "functools", "typing", "dataclasses", "enum", "abc", "pathlib",
    "os.path", "urllib.parse", "base64", "hashlib", "hmac",
    "textwrap", "string", "io", "csv", "statistics",
    "random", "uuid", "copy", "operator", "contextlib",
    "logging", "pprint", "traceback",
}

ALLOWED_THIRD_PARTY = {
    "requests", "beautifulsoup4", "bs4", "pillow", "PIL",
    "numpy", "pandas", "matplotlib", "aiohttp",
    "pydantic", "httpx", "yeelight", "kasa", "tinytuya",
}

BLOCKED_IMPORTS = {
    "subprocess", "shutil", "ctypes", "importlib", "os.system",
    "sys", "multiprocessing", "threading", "signal",
    "socket", "http.server", "xmlrpc", "ftplib", "telnetlib",
    "pickle", "shelve", "marshal",
    "code", "codeop", "compile", "compileall",
    "webbrowser", "antigravity",
    "__builtin__", "builtins",
}

BLOCKED_IMPORT_PREFIXES = {"_", "win32", "pywin32", "pyautogui", "pywinauto"}

ALLOWED_PIP_DEFAULTS = {
    "requests", "beautifulsoup4", "pillow", "numpy", "pandas",
    "matplotlib", "aiohttp", "httpx", "pydantic",
    "python-kasa", "yeelight", "tinytuya",
}


class SecurityPolicy:
    def __init__(self):
        settings = get_settings()
        self.production_mode = settings.production_mode
        user_allowed = {p.strip() for p in settings.allowed_packages.split(",") if p.strip()}
        self.allowed_pip_packages = ALLOWED_PIP_DEFAULTS | user_allowed
        self.max_execution_time = 30
        self.max_memory_mb = 256

    def validate_imports(self, code: str) -> list[str]:
        """Parse code AST and return list of import violations."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return [f"Syntax error: {e}"]

        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    v = self._check_module(alias.name)
                    if v:
                        violations.append(v)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    v = self._check_module(node.module)
                    if v:
                        violations.append(v)

        return violations

    def _check_module(self, module: str) -> str | None:
        top_level = module.split(".")[0]

        if module in BLOCKED_IMPORTS or top_level in BLOCKED_IMPORTS:
            return f"Blocked import: {module}"

        for prefix in BLOCKED_IMPORT_PREFIXES:
            if top_level.startswith(prefix):
                return f"Blocked import prefix: {module} (matches '{prefix}')"

        if top_level == "core":
            return None

        if module in ALLOWED_STDLIB or top_level in ALLOWED_STDLIB:
            return None
        if module in ALLOWED_THIRD_PARTY or top_level in ALLOWED_THIRD_PARTY:
            return None

        return None

    def validate_code_safety(self, code: str) -> list[str]:
        """Check for dangerous patterns beyond imports."""
        violations = []
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return ["Syntax error in code"]

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = self._get_call_name(node)
                if func_name in ("eval", "exec", "compile", "__import__", "globals", "locals"):
                    violations.append(f"Dangerous built-in call: {func_name}()")
                if func_name in ("os.system", "os.popen", "os.exec", "os.spawn"):
                    violations.append(f"Dangerous OS call: {func_name}()")

            if isinstance(node, ast.Attribute):
                if node.attr in ("__subclasses__", "__bases__", "__mro__", "__globals__"):
                    violations.append(f"Dangerous attribute access: .{node.attr}")

        return violations

    def _get_call_name(self, node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            parts = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        return ""

    def is_package_allowed(self, package: str) -> bool:
        clean = re.sub(r'[^a-zA-Z0-9_.\-]', '', package).lower()
        if self.production_mode:
            return clean in {p.lower() for p in self.allowed_pip_packages}
        return True

    def is_command_allowed(self, command: list[str]) -> bool:
        if not command:
            return False
        blocked_executables = {
            "rm", "del", "format", "mkfs", "dd",
            "curl", "wget", "ssh", "scp", "ftp",
            "powershell", "cmd", "bash", "sh",
            "net", "netsh", "reg", "regedit",
        }
        exe = command[0].lower().replace(".exe", "").split("\\")[-1].split("/")[-1]
        return exe not in blocked_executables

    def full_validate(self, code: str) -> list[str]:
        """Run all validations on code. Returns list of violations (empty = safe)."""
        violations = []
        violations.extend(self.validate_imports(code))
        violations.extend(self.validate_code_safety(code))
        return violations
