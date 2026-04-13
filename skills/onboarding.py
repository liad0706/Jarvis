"""Onboarding skill — interviews the user and fills USER.md with their profile."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

USER_MD_PATH = Path(__file__).resolve().parent.parent / "USER.md"
USER_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "USER.example.md"

QUESTIONS = [
    ("name",         "מה השם שלך?"),
    ("callsign",     "איך תרצה שאקרא לך? (כינוי / שם בית)"),
    ("age",          "בן / בת כמה אתה?"),
    ("location",     "איפה אתה גר? (עיר / מדינה)"),
    ("timezone",     "מה אזור הזמן שלך? (לדוגמה: Asia/Jerusalem)"),
    ("languages",    "באילו שפות אתה מדבר?"),
    ("phone",        "מה מספר הטלפון שלך? (אופציונלי — אשמור רק לצורך התראות)"),
    ("vision",       "מה החזון שלך? לאן אתה רוצה להגיע בחיים?"),
    ("values",       "מה הכי חשוב לך בחיים? (ערכים, עקרונות)"),
    ("personality",  "איך היית מתאר את עצמך? נקודות חוזק, חולשות, דרך לימוד מועדפת?"),
    ("pc_specs",     "מה המפרט של המחשב הראשי שלך?"),
    ("other_devices","אילו מכשירים נוספים יש לך? (טלפון, מדפסת 3D, Raspberry Pi, קונסולה...)"),
    ("skills",       "מה הטכנולוגיות שאתה מכיר? (שפות תכנות, כלים, frameworks)"),
    ("people",       "תאר אנשים חשובים בחייך שאני צריך להכיר (משפחה, חברים, כינויים)"),
    ("school_work",  "מה הלימודים / עבודה שלך? במה אתה צריך עזרה?"),
    ("projects",     "מה הפרויקטים שאתה עובד עליהם עכשיו?"),
    ("preferences",  "מה העדפות שלך בעבודה? (local/cloud, מהיר/מושלם, style...)"),
]


class OnboardingSkill(BaseSkill):
    name = "onboarding"
    description = "Interview the user and fill USER.md with their profile. Run once on first setup."

    def __init__(self):
        self.settings = None
        self._answers: dict[str, str] = {}

    async def do_start(self) -> dict:
        """Start the full onboarding interview and fill USER.md."""
        logger.info("Starting onboarding interview")
        self._answers = {}

        # Check if already filled
        content = _read_user_md()
        if USER_MD_PATH.exists() and "[unknown]" not in content:
            return {
                "status": "already_done",
                "message": "USER.md כבר מלא. הפעל do_reset כדי להתחיל מחדש.",
            }

        return {
            "status": "ready",
            "message": (
                "אני הולך לשאול אותך כמה שאלות כדי להכיר אותך טוב יותר. "
                f"יש {len(QUESTIONS)} שאלות — ענה כמה שאתה רוצה, אפשר לדלג עם 'דלג'.\n\n"
                "**שאלה 1:** " + QUESTIONS[0][1]
            ),
            "next_action": "answer",
            "question_index": 0,
            "question_key": QUESTIONS[0][0],
        }

    async def do_answer(self, question_key: str, answer: str, question_index: int = 0) -> dict:
        """Submit an answer for a question and get the next one."""
        # Save answer (skip if user said דלג/skip)
        skip_words = {"דלג", "skip", "pass", "לא יודע", "n/a", "-"}
        if answer.strip().lower() not in skip_words:
            self._answers[question_key] = answer.strip()

        next_index = question_index + 1

        # More questions?
        if next_index < len(QUESTIONS):
            next_key, next_q = QUESTIONS[next_index]
            return {
                "status": "continue",
                "message": f"**שאלה {next_index + 1}/{len(QUESTIONS)}:** {next_q}",
                "next_action": "answer",
                "question_index": next_index,
                "question_key": next_key,
            }

        # All done — write USER.md
        result = await self._write_user_md()
        return result

    async def do_reset(self) -> dict:
        """Reset USER.md to blank template so onboarding can run again."""
        USER_MD_PATH.write_text(_default_user_md(), encoding="utf-8")
        self._answers = {}
        return {"status": "reset", "message": "USER.md אופס. הפעל do_start כדי להתחיל מחדש."}

    async def do_status(self) -> dict:
        """Check if USER.md has been filled."""
        content = _read_user_md()
        filled = USER_MD_PATH.exists() and content.count("[unknown]") == 0
        return {
            "filled": filled,
            "unknown_count": content.count("[unknown]"),
            "message": "USER.md מלא ✓" if filled else f"USER.md חסר {content.count('[unknown]')} שדות — הפעל do_start",
        }

    async def do_update_field(self, field: str, value: str) -> dict:
        """Update a single field in USER.md (e.g. field='name', value='John')."""
        content = _read_user_md(create_if_missing=True)
        # Map field name to markdown patterns
        field_map = {
            "name":     ("**Name:**", f"**Name:** {value}"),
            "callsign": ("**Call sign:**", f"**Call sign:** {value}"),
            "age":      ("**Age:**", f"**Age:** {value}"),
            "location": ("**Location:**", f"**Location:** {value}"),
            "timezone": ("**Timezone:**", f"**Timezone:** {value}"),
            "languages":("**Languages:**", f"**Languages:** {value}"),
            "phone":    ("**Phone:**", f"**Phone:** {value}"),
        }
        if field not in field_map:
            return {"error": f"Unknown field '{field}'. Supported: {list(field_map.keys())}"}

        search, replace = field_map[field]
        # Find and replace the line
        lines = content.splitlines()
        updated = False
        for i, line in enumerate(lines):
            if search in line:
                lines[i] = f"- {replace}"
                updated = True
                break
        if not updated:
            return {"error": f"Field '{field}' not found in USER.md"}

        USER_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"status": "ok", "message": f"עדכנתי {field} ל-{value}"}

    # ── Internal helpers ──────────────────────────────────────────────

    async def _write_user_md(self) -> dict:
        """Build and write USER.md from collected answers."""
        a = self._answers

        def val(key: str, default: str = "[unknown]") -> str:
            return a.get(key, default)

        content = f"""# USER.md — Who I'm Helping

> Auto-filled by Jarvis onboarding on {_today()}.
> To update a field: "עדכן את השם שלי ל..." or run the onboarding skill again.

## Basic Info

- **Name:** {val("name")}
- **Call sign:** {val("callsign")}
- **Age:** {val("age")}
- **Location:** {val("location")}
- **Timezone:** {val("timezone")}
- **Languages:** {val("languages")}
- **Phone:** {val("phone")}

## Vision

{val("vision")}

## Core Values

{val("values")}

## Personality Patterns

{val("personality")}

## Tech Stack & Hardware

### Main PC
{val("pc_specs")}

### Other Devices
{val("other_devices")}

### Software Skills
{val("skills")}

## People (Contact Map)

{val("people")}

## School / Work

{val("school_work")}

## Current Projects

{val("projects")}

## Preferences Summary

{val("preferences")}
"""
        USER_MD_PATH.write_text(content, encoding="utf-8")
        logger.info("USER.md written with %d fields", len(self._answers))

        return {
            "status": "done",
            "fields_filled": len(self._answers),
            "fields_skipped": len(QUESTIONS) - len(self._answers),
            "message": (
                f"✅ USER.md עודכן עם {len(self._answers)}/{len(QUESTIONS)} שדות. "
                "עכשיו אני מכיר אותך הרבה יותר טוב!"
            ),
        }


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def _default_user_md() -> str:
    if USER_TEMPLATE_PATH.exists():
        return USER_TEMPLATE_PATH.read_text(encoding="utf-8")
    return _blank_template()


def _read_user_md(create_if_missing: bool = False) -> str:
    if USER_MD_PATH.exists():
        return USER_MD_PATH.read_text(encoding="utf-8")
    content = _default_user_md()
    if create_if_missing:
        USER_MD_PATH.write_text(content, encoding="utf-8")
    return content


def _blank_template() -> str:
    return """# USER.md — Who I'm Helping

> This file is filled automatically by Jarvis during onboarding.
> Run: ask jarvis "who am i" or trigger the onboarding skill.

## Basic Info

- **Name:** [unknown]
- **Call sign:** [unknown]
- **Age:** [unknown]
- **Location:** [unknown]
- **Timezone:** [unknown]
- **Languages:** [unknown]
- **Phone:** [unknown]

## Vision

[unknown]

## Core Values

[unknown]

## Personality Patterns

[unknown]

## Tech Stack & Hardware

### Main PC
[unknown]

### Other Devices
[unknown]

### Software Skills
[unknown]

## People (Contact Map)

[unknown]

## School / Work

[unknown]

## Current Projects

| Project | Description | Status |
|---------|-------------|--------|
| | | |

## Preferences Summary

[unknown]
"""
