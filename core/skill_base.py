"""Base class for all Jarvis skills and the skill registry."""

from __future__ import annotations

import asyncio
import inspect
import logging
from abc import ABC
import types
from typing import Any, Union, get_args, get_origin, get_type_hints

logger = logging.getLogger(__name__)

_BOOL_TRUE = {"true", "1", "yes", "on"}
_BOOL_FALSE = {"false", "0", "no", "off"}


def _unwrap_optional(annotation: Any) -> Any:
    """Return the inner type for Optional[T], otherwise the original annotation."""
    origin = get_origin(annotation)
    if origin not in (types.UnionType, Union):
        return annotation

    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    if len(args) == 1:
        return args[0]
    return annotation


def _json_schema_type(annotation: Any) -> str:
    """Map a Python annotation to a simple JSON schema type."""
    annotation = _unwrap_optional(annotation)
    if annotation is int:
        return "integer"
    if annotation is bool:
        return "boolean"
    if annotation is float:
        return "number"
    return "string"


def _coerce_value(value: Any, annotation: Any) -> Any:
    """Best-effort coercion for primitive tool-call argument types."""
    if value is None:
        return None

    annotation = _unwrap_optional(annotation)
    if annotation is int and not isinstance(value, int):
        return int(value)
    if annotation is float and not isinstance(value, (int, float)):
        return float(value)
    if annotation is bool and not isinstance(value, bool):
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in _BOOL_TRUE:
                return True
            if lowered in _BOOL_FALSE:
                return False
            raise ValueError(f"Cannot coerce '{value}' to bool")
        if isinstance(value, (int, float)):
            return bool(value)

    return value


class BaseSkill(ABC):
    """Every skill must subclass this. Override execute() only for custom dispatch logic."""

    name: str = "unnamed"
    description: str = ""
    RISK_MAP: dict[str, str] = {}  # action -> risk level; override in subclasses

    @staticmethod
    def _get_resolved_annotations(method) -> dict[str, Any]:
        """Resolve postponed annotations without failing on unknown forward refs."""
        try:
            return get_type_hints(method)
        except Exception:
            return {}

    def coerce_params(self, action: str, params: dict | None = None) -> dict | None:
        """Coerce incoming params to the annotated primitive types for ``do_<action>``."""
        method = getattr(self, f"do_{action}", None)
        if not method or not params:
            return params

        try:
            sig = inspect.signature(method)
        except (ValueError, TypeError):
            return params

        hints = self._get_resolved_annotations(method)
        casted = dict(params)
        for pname, param in sig.parameters.items():
            if pname == "self" or pname not in casted:
                continue

            annotation = hints.get(pname, param.annotation)
            if annotation is inspect.Parameter.empty:
                continue

            try:
                casted[pname] = _coerce_value(casted[pname], annotation)
            except (TypeError, ValueError):
                pass

        return casted

    async def execute(self, action: str, params: dict | None = None) -> dict:
        """Run an action with optional params. Return a result dict."""
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            params = self.coerce_params(action, params)
            return await method(**(params or {}))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("%s.%s failed", self.name, action)
            return {"error": str(e)}

    def get_actions(self) -> list[str]:
        """Return list of available actions (methods starting with do_)."""
        return [
            m.replace("do_", "", 1)
            for m in dir(self)
            if m.startswith("do_") and callable(getattr(self, m))
        ]

    def as_tools(self) -> list[dict]:
        """Return Ollama-compatible tool definitions for each do_* method."""
        tools = []
        for method_name in dir(self):
            if not method_name.startswith("do_"):
                continue
            method = getattr(self, method_name)
            if not callable(method):
                continue

            action_name = method_name.replace("do_", "", 1)
            doc = method.__doc__ or f"{self.name}: {action_name}"

            # Build parameter schema from type hints
            sig = inspect.signature(method)
            hints = self._get_resolved_annotations(method)
            properties = {}
            required = []
            for pname, param in sig.parameters.items():
                if pname == "self":
                    continue
                annotation = hints.get(pname, param.annotation)
                prop = {"type": _json_schema_type(annotation), "description": pname}
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


class SkillRegistry:
    """Manages all registered skills."""

    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        logger.info("Registered skill: %s", skill.name)
        self._skills[skill.name] = skill

    def get(self, name: str) -> BaseSkill | None:
        return self._skills.get(name)

    def unregister(self, name: str) -> bool:
        """Remove a skill by name. Returns True if it existed."""
        return self._skills.pop(name, None) is not None

    def all_skills(self) -> list[BaseSkill]:
        return list(self._skills.values())

    def get_all_tools(self) -> list[dict]:
        """Aggregate tool definitions from all registered skills."""
        tools = []
        for skill in self._skills.values():
            tools.extend(skill.as_tools())
        return tools

    def resolve_tool_call(self, tool_name: str) -> tuple[BaseSkill, str] | None:
        """Given a tool name like 'spotify_play', find the skill and action."""
        for skill in self._skills.values():
            prefix = f"{skill.name}_"
            if tool_name.startswith(prefix):
                action = tool_name[len(prefix):]
                return skill, action
        return None

    def get_relevant_tools(self, query: str, max_tools: int = 20) -> list[dict]:
        """Return up to max_tools tools most relevant to the query (keyword match on description)."""
        all_tools = self.get_all_tools()
        if len(all_tools) <= max_tools:
            return all_tools
        query_words = set(query.lower().split())
        def score(tool: dict) -> int:
            desc = tool["function"].get("description", "").lower()
            return sum(1 for w in query_words if w in desc)
        return sorted(all_tools, key=score, reverse=True)[:max_tools]
