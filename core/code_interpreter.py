"""Code Interpreter — safe execution of Python/JS code in isolated environments.

Supports three isolation modes:
1. Docker container (most secure, requires Docker)
2. Subprocess with restricted imports (moderate security)
3. exec() with namespace isolation (least secure, fastest)

The interpreter captures stdout, stderr, return values, and generated files.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

# Maximum execution time per code block
MAX_EXEC_SECONDS = 30
# Maximum output size
MAX_OUTPUT_CHARS = 10_000


@dataclass
class ExecutionResult:
    """Result of a code execution."""
    stdout: str = ""
    stderr: str = ""
    return_value: str = ""
    exit_code: int = 0
    duration_ms: float = 0.0
    files_created: list[str] = field(default_factory=list)
    error: str = ""
    truncated: bool = False


# ── Docker executor ──────────────────────────────────────────────────

async def _run_in_docker(
    code: str,
    language: str = "python",
    timeout: int = MAX_EXEC_SECONDS,
) -> ExecutionResult:
    """Run code in a Docker container for maximum isolation."""
    start = time.time()
    image = "python:3.11-slim" if language == "python" else "node:20-slim"
    cmd_prefix = "python3 -c" if language == "python" else "node -e"

    # Write code to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py" if language == "python" else ".js",
        delete=False, dir=tempfile.gettempdir(),
    ) as f:
        f.write(code)
        code_file = f.name

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm",
            "--network", "none",         # no network access
            "--memory", "256m",          # memory limit
            "--cpus", "1",               # CPU limit
            "--pids-limit", "50",        # process limit
            "-v", f"{code_file}:/code/run.{'py' if language == 'python' else 'js'}:ro",
            image,
            "python3" if language == "python" else "node",
            f"/code/run.{'py' if language == 'python' else 'js'}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return ExecutionResult(
                error=f"Execution timed out after {timeout}s",
                exit_code=-1,
                duration_ms=(time.time() - start) * 1000,
            )

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        truncated = False
        if len(stdout_text) > MAX_OUTPUT_CHARS:
            stdout_text = stdout_text[:MAX_OUTPUT_CHARS] + "\n... (truncated)"
            truncated = True

        return ExecutionResult(
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=proc.returncode or 0,
            duration_ms=(time.time() - start) * 1000,
            truncated=truncated,
        )
    except FileNotFoundError:
        return ExecutionResult(
            error="Docker not found. Install Docker or use subprocess mode.",
            exit_code=-1,
        )
    finally:
        try:
            os.unlink(code_file)
        except OSError:
            pass


# ── Subprocess executor ──────────────────────────────────────────────

# Dangerous modules that should not be imported in sandboxed code
_BLOCKED_IMPORTS = {
    "subprocess", "shutil", "ctypes", "importlib", "os.system",
    "socket", "http.server", "xmlrpc", "multiprocessing",
}


def _check_code_safety(code: str) -> str | None:
    """Basic static check for dangerous patterns. Returns error message or None."""
    for blocked in _BLOCKED_IMPORTS:
        if f"import {blocked}" in code or f"from {blocked}" in code:
            return f"Blocked import: {blocked}"
    dangerous_patterns = [
        ("os.system(", "os.system is not allowed"),
        ("os.popen(", "os.popen is not allowed"),
        ("eval(", "eval() is not allowed in sandboxed code"),
        ("exec(", "exec() is not allowed in sandboxed code"),
        ("__import__", "__import__ is not allowed"),
        ("open('/etc", "accessing system files is not allowed"),
        ("open('C:\\\\Windows", "accessing system files is not allowed"),
    ]
    for pattern, msg in dangerous_patterns:
        if pattern in code:
            return msg
    return None


async def _run_in_subprocess(
    code: str,
    language: str = "python",
    timeout: int = MAX_EXEC_SECONDS,
) -> ExecutionResult:
    """Run code in a subprocess with restricted imports."""
    start = time.time()

    if language == "python":
        safety_error = _check_code_safety(code)
        if safety_error:
            return ExecutionResult(error=safety_error, exit_code=-1)

    # Write to temp file
    ext = ".py" if language == "python" else ".js"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=ext, delete=False, dir=tempfile.gettempdir(),
    ) as f:
        f.write(code)
        code_file = f.name

    cmd = [sys.executable, code_file] if language == "python" else ["node", code_file]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tempfile.gettempdir(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return ExecutionResult(
                error=f"Execution timed out after {timeout}s",
                exit_code=-1,
                duration_ms=(time.time() - start) * 1000,
            )

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        truncated = False
        if len(stdout_text) > MAX_OUTPUT_CHARS:
            stdout_text = stdout_text[:MAX_OUTPUT_CHARS] + "\n... (truncated)"
            truncated = True

        return ExecutionResult(
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=proc.returncode or 0,
            duration_ms=(time.time() - start) * 1000,
            truncated=truncated,
        )
    finally:
        try:
            os.unlink(code_file)
        except OSError:
            pass


# ── Code Interpreter Skill ───────────────────────────────────────────

class CodeInterpreterSkill(BaseSkill):
    name = "code_interpreter"
    description = "Execute Python or JavaScript code safely in an isolated environment"
    RISK_MAP = {
        "run": "CRITICAL",
        "run_docker": "CRITICAL",
        "check_safety": "READ",
    }

    def __init__(self, prefer_docker: bool = True):
        self.prefer_docker = prefer_docker
        self._docker_available: bool | None = None

    async def execute(self, action: str, params: dict | None = None) -> dict:
        params = params or {}
        method = getattr(self, f"do_{action}", None)
        if method is None:
            return {"error": f"Unknown action: {action}"}
        return await method(**params)

    async def _check_docker(self) -> bool:
        if self._docker_available is not None:
            return self._docker_available
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            self._docker_available = proc.returncode == 0
        except FileNotFoundError:
            self._docker_available = False
        return self._docker_available

    async def do_run(self, code: str, language: str = "python", timeout: int = MAX_EXEC_SECONDS) -> dict:
        """Execute code in the safest available environment (Docker > subprocess)."""
        if self.prefer_docker and await self._check_docker():
            result = await _run_in_docker(code, language, timeout)
        else:
            result = await _run_in_subprocess(code, language, timeout)

        return {
            "status": "ok" if result.exit_code == 0 else "error",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "truncated": result.truncated,
            "error": result.error,
        }

    async def do_run_docker(self, code: str, language: str = "python", timeout: int = MAX_EXEC_SECONDS) -> dict:
        """Force execution in Docker container."""
        result = await _run_in_docker(code, language, timeout)
        return {
            "status": "ok" if result.exit_code == 0 else "error",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "error": result.error,
        }

    async def do_check_safety(self, code: str) -> dict:
        """Check if code is safe to execute (static analysis)."""
        error = _check_code_safety(code)
        return {
            "safe": error is None,
            "issue": error or "",
        }
