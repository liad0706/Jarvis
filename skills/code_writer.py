"""Code writing skill — stub + open Cursor only. No LLM fills implementation in files."""

import asyncio
import inspect
import logging
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

from core.skill_base import BaseSkill
from config import get_settings

logger = logging.getLogger(__name__)

# Per-action parameter descriptions for tool-calling LLMs (OpenAI/Codex/Ollama chat).
_CODE_PARAM_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "write": {
        "prompt": (
            "Requirements and acceptance criteria only: language, inputs/outputs, libraries, edge cases. "
            "Do NOT paste full source code or markdown code fences — implementation is done in Cursor (Agent/Composer)."
        ),
        "filename": (
            "Optional file name with extension (e.g. utils.py). Leave empty for a timestamped .py under generated_code."
        ),
    },
    "edit": {
        "file_path": "Path to the existing file to open for editing.",
        "instructions": (
            "Plain-language edit requirements. Do NOT paste an entire replacement file or large code dumps — Cursor applies the change."
        ),
    },
    "run": {
        "file_path": "Path to a .py or .js file to execute (30s timeout).",
    },
}


def _find_cursor_cli() -> str | None:
    """Locate the Cursor CLI executable."""
    found = shutil.which("cursor")
    if found:
        return found
    common_paths = [
        Path.home() / "AppData" / "Local" / "Programs" / "cursor" / "resources" / "app" / "bin" / "cursor.cmd",
        Path.home() / "AppData" / "Local" / "Programs" / "cursor" / "resources" / "app" / "bin" / "cursor",
        Path("/usr/local/bin/cursor"),
        Path("/usr/bin/cursor"),
    ]
    for p in common_paths:
        if p.exists():
            return str(p)
    return None


def _comment_block(prompt: str, ext: str) -> str:
    """Format user prompt as comments for the stub file."""
    lines = prompt.strip().splitlines() or ["(empty request)"]
    if ext == ".py":
        body = "\n".join(f"# {line}" for line in lines)
        return (
            "# " + "=" * 72 + "\n"
            "# Jarvis — בקשת קוד. השלם ב-Cursor: Agent / Cmd+I (Composer) על הקובץ הזה.\n"
            "# אין מילוי מימוש על ידי מודל (OpenAI/Codex/Ollama) — רק Cursor כותב כאן קוד אמיתי.\n"
            "# " + "=" * 72 + "\n"
            f"{body}\n"
            "# " + "=" * 72 + "\n"
        )
    if ext in (".js", ".ts", ".tsx", ".jsx", ".css"):
        body = "\n".join(f" * {line}" for line in lines)
        return (
            "/*\n"
            " * Jarvis code request — finish in Cursor (Agent / Composer on this file).\n"
            " * No LLM fills implementation here — only Cursor writes real code.\n"
            f"{body}\n"
            " */\n"
        )
    if ext in (".html", ".htm"):
        body = "\n".join(f"  {line}" for line in lines)
        return (
            "<!--\n"
            "  Jarvis code request — finish in Cursor (Agent / Composer).\n"
            f"{body}\n"
            "  -->\n"
        )
    if ext in (".cpp", ".c", ".h", ".hpp"):
        body = "\n".join(f" * {line}" for line in lines)
        return (
            "/*\n"
            " * Jarvis code request — finish in Cursor.\n"
            f"{body}\n"
            " */\n"
        )
    body = "\n".join(f"# {line}" for line in lines)
    return (
        "# Jarvis code request — finish in Cursor.\n"
        f"{body}\n"
    )


def _stub_suffix_and_tail(ext: str) -> str:
    if ext == ".py":
        return "\n\n# --- implementation below ---\n\npass  # TODO: replace\n"
    if ext in (".js", ".ts", ".tsx", ".jsx"):
        return "\n\n// --- implementation below ---\n\n// TODO\n"
    if ext in (".html", ".htm"):
        return "\n\n<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\"><title>todo</title></head><body></body></html>\n"
    if ext in (".cpp", ".c"):
        return "\n\n// --- implementation below ---\n\nint main() { return 0; }\n"
    return "\n\n# --- add content below ---\n"


def _build_cursor_stub(prompt: str, filename: str) -> tuple[str, str]:
    """Return (file content, filename to use)."""
    fn = (filename or "").strip()
    if fn:
        ext = Path(fn).suffix.lower() or ".py"
        if not ext.startswith("."):
            ext = "." + ext
        name = fn if Path(fn).suffix else f"{fn}.py"
    else:
        ext = ".py"
        name = f"generated_{int(time.time())}.py"

    content = _comment_block(prompt, ext) + _stub_suffix_and_tail(ext)
    return content, name


class CodeWriterSkill(BaseSkill):
    name = "code"
    description = (
        "Create or edit code files: writes a stub with requirements in comments (or a sidecar .md for edits) "
        "and opens Cursor. Implementation is always done in Cursor (Agent/Composer), never by filling the file via an LLM."
    )

    def __init__(self):
        self.settings = get_settings()
        self.output_dir = Path(self.settings.code_output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._cursor_cli = _find_cursor_cli()

    def as_tools(self) -> list[dict]:
        """Tool schemas with explicit parameter descriptions for spec-only tool args."""
        tools = []
        hints_for = _CODE_PARAM_DESCRIPTIONS
        for method_name in dir(self):
            if not method_name.startswith("do_"):
                continue
            method = getattr(self, method_name)
            if not callable(method):
                continue

            action_name = method_name.replace("do_", "", 1)
            doc = method.__doc__ or f"{self.name}: {action_name}"
            hints = hints_for.get(action_name, {})

            sig = inspect.signature(method)
            properties = {}
            required = []
            for pname, param in sig.parameters.items():
                if pname == "self":
                    continue
                prop = {"type": "string", "description": hints.get(pname, pname)}
                if param.annotation == int:
                    prop["type"] = "integer"
                elif param.annotation == bool:
                    prop["type"] = "boolean"
                properties[pname] = prop
                if param.default is inspect.Parameter.empty:
                    required.append(pname)

            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": f"{self.name}_{action_name}",
                        "description": doc.strip(),
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }
            )
        return tools

    def _open_in_cursor(self, *paths: Path) -> bool:
        """Open one or more files in Cursor IDE."""
        if not self._cursor_cli:
            logger.warning("Cursor CLI not found, cannot open file")
            return False
        try:
            args = [self._cursor_cli] + [str(p) for p in paths]
            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:
            logger.error("Failed to open Cursor: %s", e)
            return False

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        return await method(**(params or {}))

    async def do_write(self, prompt: str, filename: str = "") -> dict:
        """Create a stub file with requirements in comments and open it in Cursor for implementation."""
        code, fname = _build_cursor_stub(prompt, filename)
        save_path = self.output_dir / Path(fname).name
        save_path.write_text(code, encoding="utf-8")
        opened = self._open_in_cursor(save_path)
        return {
            "status": "written",
            "mode": "cursor_stub",
            "file": str(save_path),
            "filename": save_path.name,
            "lines": len(code.splitlines()),
            "preview": code[:500],
            "cursor_opened": opened,
            "message": (
                "נוצר קובץ-stub עם מפרט הבקשה ונפתח ב-Cursor. "
                "השלם את המימוש שם עם Agent או Composer — אף מודל לא ממלא כאן קוד."
            ),
            "reply_to_user_hebrew": (
                "פתחתי ב-Cursor קובץ עם דרישות (stub). "
                "תן ל-Agent או ל-Composer לכתוב את הקוד — ג'רוויס לא ממלא קבצים עם מודל."
            ),
        }

    async def do_edit(self, file_path: str, instructions: str) -> dict:
        """Open the target file and a sidecar instruction file in Cursor; file body is not rewritten by an LLM."""
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        sidecar = path.parent / f"{path.stem}.jarvis-edit.md"
        sidecar.write_text(
            textwrap.dedent(
                f"""\
                # Jarvis — הוראות עריכה

                **קובץ מטרה:** `{path.name}`

                ג'רוויס לא משנה את הקוד אוטומטית עם מודל. השתמש ב-Cursor Agent / Composer על הקובץ למעלה.

                ## מה לעשות

                {instructions.strip()}
                """
            ),
            encoding="utf-8",
        )
        opened = self._open_in_cursor(path, sidecar)
        return {
            "status": "edited",
            "mode": "cursor_stub",
            "file": str(path),
            "sidecar": str(sidecar),
            "cursor_opened": opened,
            "message": "נפתח הקובץ + קובץ הוראות לעריכה ב-Cursor.",
            "reply_to_user_hebrew": (
                f"לא ערכתי את `{path.name}` אוטומטית. "
                f"יצרתי `{sidecar.name}` עם ההוראות ופתחתי את שניהם ב-Cursor — שם תן ל-Agent לבצע את השינוי."
            ),
        }

    async def do_run(self, file_path: str) -> dict:
        """Run a code file (Python or Node.js only)."""
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        ext = path.suffix.lower()
        allowed = {".py": "python", ".js": "node"}
        if ext not in allowed:
            return {"error": f"Only Python (.py) and Node.js (.js) files can be run, got {ext}"}

        interpreter = allowed[ext]

        def _run():
            try:
                result = subprocess.run(
                    [interpreter, str(path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=str(path.parent),
                )
                output = result.stdout[:2000] if result.stdout else ""
                errors = result.stderr[:1000] if result.stderr else ""

                return {
                    "status": "ok" if result.returncode == 0 else "error",
                    "return_code": result.returncode,
                    "stdout": output,
                    "stderr": errors,
                    "message": "Code ran successfully" if result.returncode == 0 else f"Code failed (exit {result.returncode})",
                }
            except subprocess.TimeoutExpired:
                return {"error": "Code execution timed out (30s limit)"}
            except FileNotFoundError:
                return {"error": f"Interpreter '{interpreter}' not found. Is it installed?"}

        return await asyncio.to_thread(_run)

    async def do_list(self) -> dict:
        """List all generated code files."""
        files = []
        for f in self.output_dir.iterdir():
            if f.is_file():
                files.append({
                    "name": f.name,
                    "path": str(f),
                    "size_kb": round(f.stat().st_size / 1024, 1),
                })
        return {
            "status": "ok",
            "files": files,
            "count": len(files),
        }
