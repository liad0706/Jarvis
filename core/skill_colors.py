"""Skill/agent color manager for dashboard — adapted from Claude Code's agentColorManager.ts.

Assigns persistent colors to skills for consistent visual identification in the
dashboard and logs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 8-color palette (same as Claude Code)
SKILL_COLORS: list[str] = [
    "#E74C3C",  # red
    "#3498DB",  # blue
    "#2ECC71",  # green
    "#F1C40F",  # yellow
    "#9B59B6",  # purple
    "#E67E22",  # orange
    "#E91E8A",  # pink
    "#1ABC9C",  # cyan
]

COLOR_NAMES: list[str] = [
    "red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan",
]

# Persistent color assignments: skill_name → color index
_color_map: dict[str, int] = {}
_next_index: int = 0


def get_skill_color(skill_name: str) -> str:
    """Get the hex color assigned to a skill. Assigns one if not yet assigned."""
    global _next_index
    if skill_name not in _color_map:
        _color_map[skill_name] = _next_index % len(SKILL_COLORS)
        _next_index += 1
    return SKILL_COLORS[_color_map[skill_name]]


def get_skill_color_name(skill_name: str) -> str:
    """Get the named color for a skill."""
    if skill_name not in _color_map:
        get_skill_color(skill_name)  # ensure assigned
    return COLOR_NAMES[_color_map[skill_name]]


def set_skill_color(skill_name: str, color_name: str) -> bool:
    """Manually set a skill's color by name. Returns False if color_name is invalid."""
    if color_name not in COLOR_NAMES:
        return False
    _color_map[skill_name] = COLOR_NAMES.index(color_name)
    return True


def get_all_assignments() -> dict[str, str]:
    """Return all current skill → color name assignments."""
    return {name: COLOR_NAMES[idx] for name, idx in _color_map.items()}


def get_color_for_dashboard(skill_name: str) -> dict:
    """Return a dashboard-friendly color info dict."""
    return {
        "skill": skill_name,
        "hex": get_skill_color(skill_name),
        "name": get_skill_color_name(skill_name),
    }
