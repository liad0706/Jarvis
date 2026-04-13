"""Sandbox executor — runs dynamic skill code in an isolated subprocess."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RUNNER_SCRIPT = textwrap.dedent("""\
    import importlib.util
    import inspect
    import json
    import sys
    import os

    # Force UTF-8 on Windows (cp1255 cannot encode emoji/special chars)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    def main():
        data = json.loads(sys.stdin.read())
        skill_path = data["skill_path"]
        action = data["action"]
        params = data.get("params", {})

        # Restrict environment
        for key in list(os.environ.keys()):
            if key.startswith("JARVIS_") and "OLLAMA" not in key:
                del os.environ[key]

        # Dynamic skills may depend on values stored in the project's .env file.
        # Reload it inside the sandbox process because this subprocess starts with
        # a minimal environment by design.
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.getcwd(), ".env"), override=False)
        except Exception:
            pass

        module_name = f"sandbox_{os.path.basename(skill_path).replace('.py', '')}"
        spec = importlib.util.spec_from_file_location(module_name, skill_path)
        if spec is None or spec.loader is None:
            print(json.dumps({"error": "Cannot load module"}))
            return

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        from core.skill_base import BaseSkill
        skill_cls = None
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseSkill) and obj is not BaseSkill:
                skill_cls = obj
                break

        if skill_cls is None:
            print(json.dumps({"error": "No BaseSkill subclass found"}))
            return

        instance = skill_cls()

        import asyncio
        result = asyncio.run(instance.execute(action, params))
        print(json.dumps(result, ensure_ascii=False, default=str))

    main()
""")


class SandboxExecutor:
    def __init__(self, timeout: int = 30, project_root: Path | None = None):
        self.timeout = timeout
        self.project_root = project_root or Path(__file__).resolve().parent.parent

    async def execute(self, skill_path: Path, action: str, params: dict | None = None) -> dict:
        """Run a dynamic skill in a subprocess sandbox."""
        payload = json.dumps({
            "skill_path": str(skill_path),
            "action": action,
            "params": params or {},
        })

        env = {
            "PYTHONPATH": str(self.project_root),
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": "C:\\Windows",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "JARVIS_OLLAMA_HOST": os.environ.get("JARVIS_OLLAMA_HOST", "http://127.0.0.1:11434"),
            "JARVIS_OLLAMA_MODEL": os.environ.get("JARVIS_OLLAMA_MODEL", "qwen3:8b"),
        }

        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-X", "utf8", "-c", RUNNER_SCRIPT],
                input=payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(self.project_root),
                timeout=self.timeout,
            )

            if completed.returncode != 0:
                error_text = (completed.stderr or "")[:500]
                logger.error("Sandbox process failed (exit %d): %s", completed.returncode, error_text)
                return {"error": f"Sandbox execution failed: {error_text}"}

            output = (completed.stdout or "").strip()
            if not output:
                return {"error": "Sandbox returned empty output"}

            return json.loads(output)

        except subprocess.TimeoutExpired:
            logger.error("Sandbox execution timed out after %ds", self.timeout)
            return {"error": f"Sandbox execution timed out ({self.timeout}s)"}
        except json.JSONDecodeError as e:
            logger.error("Sandbox returned invalid JSON: %s", e)
            return {"error": f"Sandbox returned invalid output: {output[:200]}"}
        except Exception as e:
            logger.exception("Sandbox execution error")
            return {"error": f"Sandbox error: {e}"}
