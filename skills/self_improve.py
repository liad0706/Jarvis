"""Self-improvement meta-skill — generates, validates, and hot-loads new skills at runtime."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

from config import get_settings
from core.dynamic_loader import import_skill_module, DYNAMIC_SKILLS_DIR
from core.policy import SecurityPolicy
from core.skill_base import BaseSkill, SkillRegistry
from core.verification import verify_skill_file

logger = logging.getLogger(__name__)

MANIFEST_PATH = DYNAMIC_SKILLS_DIR / "manifest.json"

MAX_GENERATION_RETRIES = 3

SKILL_GENERATION_PROMPT = """You are an expert Python developer. Generate a complete, working Jarvis skill module.

=== BASE CLASS (you MUST follow this interface exactly) ===

```python
from abc import ABC, abstractmethod

class BaseSkill(ABC):
    name: str = "unnamed"
    description: str = ""

    @abstractmethod
    async def execute(self, action: str, params: dict | None = None) -> dict:
        ...

    def get_actions(self) -> list[str]:
        return [m.replace("do_", "", 1) for m in dir(self)
                if m.startswith("do_") and callable(getattr(self, m))]
```

=== EXAMPLE SKILL (follow this pattern EXACTLY) ===

```python
import logging
from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

class WeatherSkill(BaseSkill):
    name = "weather"
    description = "Check current weather for a city"
    REQUIREMENTS: list[str] = ["requests"]

    def __init__(self):
        self.api_url = "https://wttr.in/"

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{{action}}", None)
        if not method:
            return {{"error": f"Unknown action: {{action}}"}}
        try:
            return await method(**(params or {{}}))
        except Exception as e:
            return {{"error": f"Unexpected error in {{action}}: {{e}}"}}

    async def do_check(self, city: str) -> dict:
        \"\"\"Check the current weather for a given city.\"\"\"
        import asyncio
        import requests
        try:
            resp = await asyncio.to_thread(
                requests.get,
                self.api_url + city,
                params={{"format": "j1"}},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            current = data["current_condition"][0]
            return {{
                "status": "ok",
                "city": city,
                "temp_c": current["temp_C"],
                "description": current["weatherDesc"][0]["value"],
            }}
        except Exception as e:
            return {{"error": f"Weather check failed for {{city}}: {{e}}"}}
```

=== IMPORT RULES ===

1. ALL imports go at the TOP of the file (standard library and core imports).
2. Use absolute imports: `from core.skill_base import BaseSkill` — NEVER relative imports.
3. Third-party packages (pip-installed) should be imported INSIDE methods (lazy import) so the module loads even if they are missing.
4. Standard library imports (os, json, asyncio, pathlib, etc.) go at the TOP of the file.

=== ASYNC RULES ===

5. ALL do_* methods MUST be `async def`, no exceptions.
6. The execute() method MUST be `async def`.
7. For blocking I/O calls (requests.get, file I/O, subprocess), wrap them in `await asyncio.to_thread(...)`.
   Example: `resp = await asyncio.to_thread(requests.get, url, timeout=10)`
8. NEVER use time.sleep() — use `await asyncio.sleep()` instead.

=== RETURN VALUE RULES ===

9. EVERY do_* method MUST return a `dict`. NEVER return None, str, list, or anything else.
10. Success: return {{"status": "ok", ...extra_data...}}
11. Failure: return {{"error": "description of what went wrong"}}
12. NEVER raise exceptions from do_* methods — always catch and return {{"error": str(e)}}.

=== STRUCTURE RULES ===

13. Create exactly ONE class that inherits from BaseSkill.
14. The class MUST have `name = "..."` (short, lowercase, underscores OK) as a class attribute.
15. The class MUST have `description = "..."` (one sentence) as a class attribute.
16. Implement `async def execute(self, action, params)` that dispatches to do_* methods.
17. execute() MUST have try/except around the method dispatch to catch ALL errors.
18. Every capability is an `async def do_<action>(self, ...)` method with type-hinted params and a docstring.
19. If you need pip packages, set `REQUIREMENTS: list[str] = ["package1", "package2"]` as a class variable.
20. Always add `def __init__(self)` that initializes any instance attributes the skill needs.
21. Return ONLY the Python code. No markdown fences, no explanations, no comments outside the code.

=== CRITICAL — DO NOT BREAK THESE ===

22. NEVER use __aenter__ / __aexit__ — skills are NOT used as context managers.
23. Every resource (connection, client, file handle) MUST be created INSIDE the do_* method, used, and cleaned up in the same call. NEVER store persistent connections on self.
24. If you need settings, import `from config import get_settings` and call it in __init__: `self.settings = get_settings()`.
25. NEVER reference `self.<attribute>` in a do_* method unless you set it in __init__ or earlier in the SAME method.

=== COMMON MISTAKES TO AVOID ===

26. WRONG: `def do_something(self, x):` — MUST be `async def do_something(self, x) -> dict:`
27. WRONG: `return "done"` — MUST be `return {{"status": "ok", "message": "done"}}`
28. WRONG: `raise ValueError(...)` inside do_* — MUST catch and return {{"error": ...}}
29. WRONG: `import some_pip_package` at top of file — third-party imports go INSIDE methods.
30. WRONG: `self.conn = create_connection()` in __init__ — connections go in do_* methods.
31. WRONG: Missing execute() method or missing do_* methods.
32. WRONG: `def execute(...)` without async — MUST be `async def execute(...)`.
33. WRONG: Placeholder code like `pass` or `# TODO` — write REAL, COMPLETE, WORKING code.
34. WRONG: `requests.get(...)` without `await asyncio.to_thread(...)` — blocking calls MUST be wrapped.
35. NEVER write dummy/placeholder implementations. Every method must contain real, working logic.
36. THINK step by step: Does this code actually work? Will this API call succeed? Are the keys correct?
37. WRONG: Using wrong import names (e.g. `import pillow` instead of `from PIL import Image`).
38. WRONG: Calling APIs that don't exist or using wrong URL patterns.
39. WRONG: Not handling missing/empty params — always provide defaults or check before using.
40. WRONG: Hardcoding file paths — use `pathlib.Path` and relative paths.
41. ALWAYS test your logic mentally: trace through the code line by line and verify it works.
42. If the skill calls an external API, use a well-known, stable, FREE API (no API key required if possible).
43. If the skill needs an API key, read it from settings: `self.settings = get_settings()` in __init__.

=== TASK ===

Create a skill for: {capability_description}
Skill name should be: {skill_name}
"""


def _strip_markdown_fences(code: str) -> str:
    if code.startswith("```"):
        lines = code.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
    return code


def _read_manifest() -> dict:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"skills": {}}


def _write_manifest(data: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _validate_skill_code(code: str) -> str | None:
    """Validate generated code. Returns error message or None if valid."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"Syntax error: {e}"

    skill_classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ""
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name == "BaseSkill":
                    skill_classes.append(node.name)

    if not skill_classes:
        return "No class inheriting from BaseSkill found."

    cls_node = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == skill_classes[0]
    )

    method_names = [
        n.name for n in ast.walk(cls_node)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    if "execute" not in method_names:
        return f"Class {skill_classes[0]} is missing the execute() method."

    do_methods = [m for m in method_names if m.startswith("do_")]
    if not do_methods:
        return f"Class {skill_classes[0]} has no do_* action methods."

    # --- Extended validations ---

    # Block __aenter__/__aexit__ — skills are not context managers
    if "__aenter__" in method_names or "__aexit__" in method_names:
        return (
            f"Class {skill_classes[0]} uses __aenter__/__aexit__. "
            "Skills are NOT context managers — create/cleanup resources inside each do_* method."
        )

    # Collect attributes set in __init__
    init_attrs: set[str] = set()
    for node in ast.walk(cls_node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
            for assign in ast.walk(node):
                if isinstance(assign, ast.Assign):
                    for target in assign.targets:
                        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                            init_attrs.add(target.attr)
                elif isinstance(assign, ast.AnnAssign) and assign.target:
                    if isinstance(assign.target, ast.Attribute) and isinstance(assign.target.value, ast.Name) and assign.target.value.id == "self":
                        init_attrs.add(assign.target.attr)

    # Also include class-level attributes (like name, description, REQUIREMENTS)
    for node in cls_node.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    init_attrs.add(target.id)
        elif isinstance(node, ast.AnnAssign) and node.target and isinstance(node.target, ast.Name):
            init_attrs.add(node.target.id)

    # Check do_* methods for self.X reads where X was never set in __init__
    for node in ast.walk(cls_node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("do_"):
            # Gather attrs set within this do_* method
            local_attrs: set[str] = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                            local_attrs.add(t.attr)

            # Check attrs read in this method
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "self":
                    attr = sub.attr
                    # Skip methods, dunder, and known safe patterns
                    if attr.startswith("__") or attr in ("settings", "registry", "policy", "name", "description"):
                        continue
                    if callable(getattr(BaseSkill, attr, None)):
                        continue
                    if attr not in init_attrs and attr not in local_attrs and not attr.startswith("do_"):
                        # Check if it's a method defined on the class
                        if attr not in method_names:
                            return (
                                f"Method do_{node.name.removeprefix('do_')}() reads self.{attr} "
                                f"but it's never set in __init__ or in the method itself. "
                                "Initialize it in __init__ or create it locally."
                            )

    # Check execute() has try/except
    for node in ast.walk(cls_node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "execute":
            has_try = any(isinstance(sub, ast.Try) for sub in ast.walk(node))
            if not has_try:
                return (
                    "execute() method must have try/except to handle errors gracefully. "
                    "Wrap the method dispatch in try/except and return {'error': str(e)} on failure."
                )

    # Check execute() is async
    for node in ast.walk(cls_node):
        if isinstance(node, ast.FunctionDef) and node.name == "execute":
            return (
                "execute() must be 'async def execute(...)' not 'def execute(...)'. "
                "Add the async keyword."
            )

    # Check ALL do_* methods are async
    for node in ast.walk(cls_node):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("do_"):
            return (
                f"Method {node.name}() must be 'async def {node.name}(...)' not 'def {node.name}(...)'. "
                "ALL do_* methods must be async."
            )

    # Check that 'name' and 'description' class attributes exist
    cls_attr_names = set()
    for node in cls_node.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    cls_attr_names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and node.target and isinstance(node.target, ast.Name):
            cls_attr_names.add(node.target.id)
    if "name" not in cls_attr_names:
        return (
            f"Class {skill_classes[0]} is missing the 'name' class attribute. "
            "Add: name = \"your_skill_name\" as a class variable."
        )
    if "description" not in cls_attr_names:
        return (
            f"Class {skill_classes[0]} is missing the 'description' class attribute. "
            "Add: description = \"what this skill does\" as a class variable."
        )

    # Check do_* methods return dicts (look for bare returns or return of non-dict)
    for node in ast.walk(cls_node):
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("do_"):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return):
                    if sub.value is None:
                        return (
                            f"Method {node.name}() has a bare 'return' or 'return None'. "
                            "ALL do_* methods must return a dict, e.g. return {{'status': 'ok'}}."
                        )
                    # Check for return of string constant
                    if isinstance(sub.value, ast.Constant) and isinstance(sub.value.value, str):
                        return (
                            f"Method {node.name}() returns a string. "
                            "ALL do_* methods must return a dict, e.g. return {{'status': 'ok', 'message': '...'}}."
                        )

    # Check for placeholder/dummy implementations (pass-only or ellipsis-only do_* bodies)
    for node in ast.walk(cls_node):
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("do_"):
            # Filter out docstrings to get actual body statements
            body = [
                stmt for stmt in node.body
                if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str))
            ]
            if len(body) == 0:
                return (
                    f"Method {node.name}() has no implementation (only a docstring). "
                    "Write real, working code — no placeholders."
                )
            if len(body) == 1:
                stmt = body[0]
                if isinstance(stmt, ast.Pass):
                    return (
                        f"Method {node.name}() only contains 'pass'. "
                        "Write real, working code — no placeholders."
                    )
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is ...:
                    return (
                        f"Method {node.name}() only contains '...'. "
                        "Write real, working code — no placeholders."
                    )
            if len(body) < 3:
                return (
                    f"Method {node.name}() has only {len(body)} statement(s) (excluding docstring). "
                    "This is too trivial — write a real implementation with at least 3 lines of logic."
                )

    # Reject bare print() calls — skills should use logging
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == "print":
                return (
                    "Do NOT use print() in skills — use 'import logging; "
                    "logger = logging.getLogger(__name__)' and logger.info/warning/error instead."
                )

    # Reject empty except blocks (except ...: pass)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            real_body = [
                s for s in node.body
                if not isinstance(s, ast.Pass)
            ]
            if not real_body:
                return (
                    "Empty 'except: pass' found — never silently swallow errors. "
                    "At minimum log the error: logger.warning('...', exc_info=True) "
                    "or re-raise, or return {'error': str(e)}."
                )

    return None


def _extract_requirements(code: str) -> list[str]:
    """Pull REQUIREMENTS list from the generated code."""
    match = re.search(r'REQUIREMENTS\s*(?::\s*list\[str\])?\s*=\s*\[([^\]]*)\]', code)
    if not match:
        return []
    raw = match.group(1)
    return [s.strip().strip("'\"") for s in raw.split(",") if s.strip().strip("'\"")]


class SelfImproveSkill(BaseSkill):
    name = "self_improve"
    description = (
        "Create new skills dynamically, edit existing code, read source files, "
        "and create new files. ALWAYS start with project_map to see what's where, "
        "then read_file for specific files. Use edit_file to fix bugs or modify behavior, "
        "create_file for new files, and create for generating new skills from scratch."
    )

    def __init__(self, registry: SkillRegistry):
        self.registry = registry
        self.settings = get_settings()
        self.policy = SecurityPolicy()
        self.__provider = None
        DYNAMIC_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def _provider(self):
        """Lazy-init the LLM provider — uses the configured provider (Codex/Claude/OpenAI) for better code quality."""
        if self.__provider is None:
            from core.providers import get_provider
            self.__provider = get_provider(self.settings)
            logger.info("Self-improve using LLM provider: %s", self.__provider.name)
        return self.__provider

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        return await method(**(params or {}))

    def _get_related_skill_code(self, capability_description: str, skill_name: str) -> str:
        """Find existing skills that might be related and return their source code as context."""
        skills_dir = Path(__file__).resolve().parent
        context_parts = []

        # Keywords from the request
        keywords = set(re.findall(r'[a-z_]+', capability_description.lower() + " " + skill_name.lower()))

        for py_file in skills_dir.glob("*.py"):
            if py_file.name.startswith("__"):
                continue
            try:
                code = py_file.read_text(encoding="utf-8")
                file_lower = py_file.stem.lower()
                # Check if this skill is related
                if any(kw in file_lower for kw in keywords if len(kw) > 3):
                    # Truncate very long files
                    if len(code) > 4000:
                        code = code[:4000] + "\n# ... (truncated)"
                    context_parts.append(f"=== EXISTING SKILL: {py_file.name} ===\n{code}")
            except Exception:
                pass

        if not context_parts:
            return ""

        return (
            "\n\n=== EXISTING RELATED SKILLS (use these as reference for patterns, imports, connections) ===\n"
            "IMPORTANT: Reuse the same connection patterns, credential handling, and library usage as the existing skills.\n"
            "Do NOT invent new APIs — look at how the existing code works and follow the same approach.\n\n"
            + "\n\n".join(context_parts[:3])  # Max 3 related skills
        )

    async def do_create(self, capability_description: str, skill_name: str = "") -> dict:
        """Create a brand-new skill. Describe what the skill should do and give it a short name."""
        if not skill_name:
            skill_name = re.sub(r'[^a-z0-9]+', '_', capability_description.lower())[:30].strip('_')

        skill_name = re.sub(r'[^a-z0-9_]', '', skill_name.lower())

        if self.registry.get(skill_name):
            return {"error": f"Skill '{skill_name}' already exists. Choose a different name."}

        file_path = DYNAMIC_SKILLS_DIR / f"{skill_name}.py"

        # Find related existing skills to use as reference
        related_context = self._get_related_skill_code(capability_description, skill_name)

        prompt = SKILL_GENERATION_PROMPT.format(
            capability_description=capability_description,
            skill_name=skill_name,
        )

        if related_context:
            prompt += related_context

        code = None
        last_error = None

        for attempt in range(1 + MAX_GENERATION_RETRIES):
            try:
                generation_prompt = prompt
                if last_error and attempt > 0:
                    generation_prompt += (
                        f"\n\n⚠️ PREVIOUS ATTEMPT #{attempt} FAILED. You MUST fix this error:\n"
                        f"ERROR: {last_error}\n\n"
                        "INSTRUCTIONS FOR FIXING:\n"
                        "- Re-read ALL the rules above very carefully before writing code.\n"
                        "- ALL do_* methods MUST be 'async def' and return a dict.\n"
                        "- execute() MUST be async and have try/except around dispatch.\n"
                        "- 'name' and 'description' MUST be set as class attributes.\n"
                        "- Do NOT use placeholder code (pass, ..., TODO) — write REAL logic.\n"
                        "- Every do_* method must have at least 3 real lines of logic (not counting docstring).\n"
                        "- Wrap ALL blocking I/O in await asyncio.to_thread(...).\n"
                        "- Third-party imports go INSIDE methods, not at top of file.\n"
                        "- NEVER use __aenter__/__aexit__ or store connections on self.\n"
                        "- Test mentally: does this code actually run? Are the APIs/URLs correct?\n"
                        "- Regenerate the COMPLETE corrected code from scratch.\n"
                    )

                llm_response = await self._provider.chat(
                    messages=[
                        {"role": "system", "content": (
                            "You are an expert Python developer. Output ONLY valid Python code, nothing else. "
                            "No markdown fences, no explanations before or after the code. "
                            "The code must be production-quality: proper error handling, correct async/await, "
                            "real working logic (not placeholders). Think step by step before writing."
                        )},
                        {"role": "user", "content": generation_prompt},
                    ],
                )
                raw_code = llm_response.content.strip()
                code = _strip_markdown_fences(raw_code)

                validation_error = _validate_skill_code(code)
                if validation_error:
                    last_error = validation_error
                    logger.warning("Skill validation failed (attempt %d): %s", attempt + 1, validation_error)
                    continue

                policy_violations = self.policy.full_validate(code)
                if policy_violations:
                    last_error = "Policy violations: " + "; ".join(policy_violations)
                    logger.warning("Skill policy check failed (attempt %d): %s", attempt + 1, last_error)
                    continue

                last_error = None
                break

            except Exception as e:
                last_error = f"LLM error: {e}"
                logger.exception("Skill generation LLM error (attempt %d)", attempt + 1)

        if last_error:
            return {
                "error": (
                    f"Failed to generate valid skill after {1 + MAX_GENERATION_RETRIES} attempts. "
                    f"Last error: {last_error}. "
                    f"Suggestion: Try breaking the skill into simpler pieces, or provide a more "
                    f"detailed capability_description with specific libraries/APIs to use."
                ),
            }

        requirements = _extract_requirements(code)
        if requirements:
            blocked = [r for r in requirements if not self.policy.is_package_allowed(r)]
            if blocked:
                return {"error": f"Blocked packages: {', '.join(blocked)}. Not in allowlist."}
            install_result = await self._install_requirements(requirements)
            if install_result:
                logger.info("Installed packages: %s", install_result)

        file_path.write_text(code, encoding="utf-8")
        logger.info("Saved dynamic skill to %s", file_path)

        verification = await verify_skill_file(file_path)
        if not verification.passed:
            file_path.unlink(missing_ok=True)
            return {
                "error": (
                    "Generated skill failed verification and was removed.\n"
                    f"{verification.summary}"
                ),
            }

        skill_instance = import_skill_module(file_path)
        if skill_instance is None:
            failed_code_preview = ""
            try:
                failed_code_preview = file_path.read_text(encoding="utf-8")[:500]
            except Exception:
                pass
            file_path.unlink(missing_ok=True)
            return {
                "error": (
                    "Generated code passed static validation but failed to import at runtime. "
                    "This usually means: (1) a third-party import is missing from REQUIREMENTS, "
                    "(2) there's a NameError or AttributeError at module level, or "
                    "(3) __init__() crashes. The skill file has been removed. "
                    f"Code preview:\n{failed_code_preview}"
                ),
            }

        self.registry.register(skill_instance)
        logger.info("Registered new skill: %s", skill_instance.name)

        manifest = _read_manifest()
        manifest["skills"][skill_name] = {
            "name": skill_instance.name,
            "description": skill_instance.description,
            "file": file_path.name,
            "actions": skill_instance.get_actions(),
            "requirements": requirements,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "original_request": capability_description,
            "verification": verification.summary,
        }
        _write_manifest(manifest)

        return {
            "status": "created",
            "skill_name": skill_instance.name,
            "description": skill_instance.description,
            "actions": skill_instance.get_actions(),
            "file": str(file_path),
            "verification": verification.summary,
            "message": f"New skill '{skill_instance.name}' created with actions: {', '.join(skill_instance.get_actions())}. You can now use it.",
        }

    async def do_list(self) -> dict:
        """List all dynamically created skills."""
        manifest = _read_manifest()
        skills_info = []
        for name, info in manifest.get("skills", {}).items():
            skills_info.append({
                "name": name,
                "description": info.get("description", ""),
                "actions": info.get("actions", []),
                "created_at": info.get("created_at", ""),
            })
        return {
            "status": "ok",
            "dynamic_skills": skills_info,
            "count": len(skills_info),
        }

    async def do_remove(self, skill_name: str) -> dict:
        """Remove a dynamically created skill."""
        manifest = _read_manifest()

        if skill_name not in manifest.get("skills", {}):
            return {"error": f"Dynamic skill '{skill_name}' not found in manifest."}

        info = manifest["skills"].pop(skill_name)
        _write_manifest(manifest)

        file_path = DYNAMIC_SKILLS_DIR / info.get("file", f"{skill_name}.py")
        if file_path.exists():
            file_path.unlink()

        if self.registry.get(skill_name):
            self.registry.unregister(skill_name)

        return {
            "status": "removed",
            "skill_name": skill_name,
            "message": f"Skill '{skill_name}' has been removed.",
        }

    async def do_edit_file(self, file_path: str, old_text: str, new_text: str) -> dict:
        """Edit an existing file in the Jarvis project by replacing old_text with new_text.
        Use this to fix bugs, add features, or modify behavior in any Jarvis source file.
        file_path can be relative to project root (e.g. 'skills/morning_routine.py') or absolute."""
        from pathlib import Path

        project_root = Path(__file__).resolve().parent.parent

        # Resolve path
        p = Path(file_path)
        if not p.is_absolute():
            p = project_root / file_path
        p = p.resolve()

        # Security: must be within project directory
        try:
            p.relative_to(project_root)
        except ValueError:
            return {"error": f"Access denied — can only edit files within {project_root}"}

        # Don't allow editing certain critical files
        blocked = [".env", "credentials", "secrets", ".git"]
        if any(b in str(p).lower() for b in blocked):
            return {"error": "Cannot edit sensitive files (.env, credentials, .git)"}

        if not p.exists():
            return {"error": f"File not found: {p}"}

        try:
            content = p.read_text(encoding="utf-8")
        except Exception as e:
            return {"error": f"Cannot read file: {e}"}

        if old_text not in content:
            return {
                "error": (
                    "old_text not found in file. Make sure it matches EXACTLY, "
                    "including whitespace, indentation, and newlines. "
                    "Use read_file first to see the exact content."
                ),
            }

        count = content.count(old_text)
        new_content = content.replace(old_text, new_text, 1)

        # For Python files, validate syntax before writing
        if p.suffix == ".py":
            try:
                ast.parse(new_content)
            except SyntaxError as e:
                return {
                    "error": (
                        f"Edit would cause a syntax error and was NOT applied. "
                        f"SyntaxError at line {e.lineno}, col {e.offset}: {e.msg}. "
                        f"Fix the new_text and try again."
                    ),
                }

        # Keep a backup before writing
        backup_content = content
        try:
            p.write_text(new_content, encoding="utf-8")
        except Exception as e:
            return {"error": f"Cannot write file: {e}"}

        # Verify the write succeeded by re-reading
        try:
            verify = p.read_text(encoding="utf-8")
            if old_text in verify and old_text != new_text:
                # Something went wrong, revert
                p.write_text(backup_content, encoding="utf-8")
                return {"error": "Edit verification failed — file was reverted to original."}
        except Exception:
            pass

        return {
            "status": "ok",
            "file": str(p),
            "replacements": 1,
            "total_matches": count,
            "reply_to_user_hebrew": f"עדכנתי את {p.name} בהצלחה",
        }

    async def do_project_map(self) -> dict:
        """Get the full project map — shows all files, their purpose, and key functions.
        Use this FIRST before reading individual files, so you know where to look."""
        project_root = Path(__file__).resolve().parent.parent
        map_path = project_root / "data" / "project_map.md"
        if not map_path.exists():
            return {"error": "Project map not found. Run project map generation first."}
        content = map_path.read_text(encoding="utf-8")
        return {
            "status": "ok",
            "content": content,
            "reply_to_user_hebrew": "טעינתי את מפת הפרויקט — עכשיו אני יודע מה יש בכל קובץ.",
        }

    async def do_read_file(self, file_path: str, start_line: int | str = 0, end_line: int | str = 0) -> dict:
        """Read a file from the Jarvis project. Useful for understanding code before editing.
        file_path can be relative to project root or absolute.
        Use start_line/end_line to read a specific range (1-indexed)."""
        from pathlib import Path

        project_root = Path(__file__).resolve().parent.parent

        p = Path(file_path)
        if not p.is_absolute():
            p = project_root / file_path
        p = p.resolve()

        try:
            p.relative_to(project_root)
        except ValueError:
            return {"error": f"Access denied — can only read files within {project_root}"}

        blocked = [".env", "credentials", "secrets"]
        if any(b in str(p).lower() for b in blocked):
            return {"error": "Cannot read sensitive files"}

        if not p.exists():
            return {"error": f"File not found: {p}"}

        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            return {"error": f"Cannot read file: {e}"}

        # LLM sometimes passes strings instead of ints
        try:
            start_line = int(start_line) if start_line else 0
        except (ValueError, TypeError):
            start_line = 0
        try:
            end_line = int(end_line) if end_line else 0
        except (ValueError, TypeError):
            end_line = 0

        if start_line > 0 and end_line > 0:
            selected = lines[start_line-1:end_line]
        elif start_line > 0:
            selected = lines[start_line-1:start_line+49]  # 50 lines from start
        else:
            selected = lines[:100]  # First 100 lines by default

        numbered = [f"{i+start_line if start_line > 0 else i+1}: {line}" for i, line in enumerate(selected)]

        return {
            "status": "ok",
            "file": str(p),
            "total_lines": len(lines),
            "content": "\n".join(numbered),
        }

    async def do_create_file(self, file_path: str, content: str) -> dict:
        """Create a new file in the Jarvis project. file_path relative to project root."""
        from pathlib import Path

        project_root = Path(__file__).resolve().parent.parent

        p = Path(file_path)
        if not p.is_absolute():
            p = project_root / file_path
        p = p.resolve()

        try:
            p.relative_to(project_root)
        except ValueError:
            return {"error": f"Access denied — can only create files within {project_root}"}

        blocked = [".env", "credentials", "secrets", ".git"]
        if any(b in str(p).lower() for b in blocked):
            return {"error": "Cannot create sensitive files"}

        if p.exists():
            return {"error": f"File already exists: {p}. Use edit_file to modify it."}

        if p.suffix == ".py":
            try:
                ast.parse(content)
            except SyntaxError as e:
                return {
                    "error": (
                        f"Python syntax error — file NOT created. "
                        f"SyntaxError at line {e.lineno}, col {e.offset}: {e.msg}. "
                        f"Fix the content and try again."
                    ),
                }

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except Exception as e:
            return {"error": f"Cannot create file: {e}"}

        return {
            "status": "ok",
            "file": str(p),
            "reply_to_user_hebrew": f"יצרתי קובץ חדש: {p.name}",
        }

    async def _install_requirements(self, packages: list[str]) -> list[str]:
        """Install pip packages. Returns list of successfully installed packages."""
        installed = []
        for pkg in packages:
            pkg_clean = re.sub(r'[^a-zA-Z0-9_.\-]', '', pkg)
            if not pkg_clean:
                continue
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-m", "pip", "install", pkg_clean],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode == 0:
                    installed.append(pkg_clean)
                    logger.info("Installed package: %s", pkg_clean)
                else:
                    logger.warning("Failed to install %s: %s", pkg_clean, result.stderr[:500])
            except Exception as e:
                logger.warning("Error installing %s: %s", pkg_clean, e)
        return installed
