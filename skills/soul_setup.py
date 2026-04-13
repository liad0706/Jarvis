"""Soul Setup skill — lets the user define the assistant's personality and fill SOUL.md."""

from __future__ import annotations

import logging
from pathlib import Path

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

SOUL_MD_PATH = Path(__file__).resolve().parent.parent / "SOUL.md"
SOUL_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "SOUL.example.md"

# ── Questions ─────────────────────────────────────────────────────────
# Each entry: (key, question, options_hint)
# options_hint = None means free text; list = show choices but still accept free text

QUESTIONS = [
    (
        "name",
        "מה תהיה שם העוזר שלך?",
        ["Jarvis", "Friday", "Atlas", "Max", "Aria"],
    ),
    (
        "inspired_by",
        "מה האישיות שמעוררת השראה? (לדוגמה: JARVIS של טוני סטארק, HAL 9000, Samantha מ-Her)",
        None,
    ),
    (
        "primary_language",
        "מה השפה הראשית?",
        ["עברית", "English", "עברית + אנגלית מעורבב"],
    ),
    (
        "tone",
        "מה הסגנון הכללי של העוזר?",
        [
            "ישיר וחצוף — בלי מילוי, בלי 'שאלה מצוינת'",
            "ידידותי ונעים — חם אבל לא מגזים",
            "רשמי ומקצועי — כמו מזכיר בכיר",
            "סרקסטי וחכם — עם הומור יבש",
        ],
    ),
    (
        "verbosity",
        "כמה ארוכות התשובות?",
        [
            "קצר תמיד — משפט-שניים מקסימום",
            "מותאם לגודל המשימה — קצר לשאלה פשוטה, מפורט לפרויקט",
            "מפורט תמיד — עם הסברים, דוגמאות, צעדים",
        ],
    ),
    (
        "proactivity",
        "כמה יוזמה לקחת בלי לשאול?",
        [
            "גבוהה — לבצע עם ברירות מחדל, לדווח כשסיים",
            "בינונית — לבצע פעולות קטנות לבד, לשאול על גדולות",
            "נמוכה — תמיד לאשר לפני ביצוע",
        ],
    ),
    (
        "honesty",
        "איך להתנהג כשרעיון של המשתמש לא טוב?",
        [
            "לומר ישירות שזה לא טוב ולהסביר למה",
            "להציע אלטרנטיבה טובה יותר בלי להסביר",
            "לשאול שאלות מובילות עד שהמשתמש יגיע למסקנה בעצמו",
        ],
    ),
    (
        "mvp_mode",
        "איך להתמודד עם פרויקטים גדולים?",
        [
            "MVP תמיד — לחזור להכי קטן שעובד",
            "לפצל לשלבים ולעבוד לפי סדר",
            "לבצע הכל בבת אחת אם אפשר",
        ],
    ),
    (
        "hard_rules",
        "מה הגבולות האדומים? (דברים שהעוזר לעולם לא יעשה — אפשר לרשום כמה דברים)",
        [
            "לא למחוק קבצי מערכת, לא להוציא כסף ללא אישור",
            "לא לפעול בניגוד לחוק, לא לגעת בפרטיות אחרים",
            "הגדר בעצמך...",
        ],
    ),
    (
        "interruptions",
        "מתי להפריע למשתמש בזמן עבודה?",
        [
            "רק כשסיימתי או כשיש בעיה חמורה שחוסמת",
            "עדכון קצר כל דקה במשימות ארוכות",
            "לשאול לפני כל צעד לא ברור",
        ],
    ),
    (
        "extra",
        "יש עוד משהו שחשוב לך שהעוזר ידע על עצמו או על אופן העבודה?",
        None,
    ),
]


class SoulSetupSkill(BaseSkill):
    name = "soul_setup"
    description = "Define the assistant's personality, communication style, and rules — fills SOUL.md."

    def __init__(self):
        self.settings = None
        self._answers: dict[str, str] = {}

    async def do_start(self) -> dict:
        """Start the soul setup interview."""
        content = _read_soul_md()
        if SOUL_MD_PATH.exists() and "[undefined]" not in content:
            return {
                "status": "already_done",
                "message": "SOUL.md כבר מוגדר. הפעל do_reset כדי להגדיר מחדש.",
            }

        self._answers = {}
        key, question, options = QUESTIONS[0]
        hint = _format_options(options)

        return {
            "status": "ready",
            "message": (
                f"בוא נגדיר את אישיות העוזר שלך! יש {len(QUESTIONS)} שאלות קצרות.\n\n"
                f"**שאלה 1/{len(QUESTIONS)}:** {question}{hint}"
            ),
            "next_action": "answer",
            "question_index": 0,
            "question_key": key,
            "options": options or [],
        }

    async def do_answer(self, question_key: str, answer: str, question_index: int = 0) -> dict:
        """Submit an answer and get the next question."""
        skip_words = {"דלג", "skip", "pass", "-", "לא יודע"}
        if answer.strip().lower() not in skip_words:
            self._answers[question_key] = answer.strip()

        next_index = question_index + 1

        if next_index < len(QUESTIONS):
            key, question, options = QUESTIONS[next_index]
            hint = _format_options(options)
            return {
                "status": "continue",
                "message": f"**שאלה {next_index + 1}/{len(QUESTIONS)}:** {question}{hint}",
                "next_action": "answer",
                "question_index": next_index,
                "question_key": key,
                "options": options or [],
            }

        return await self._write_soul_md()

    async def do_reset(self) -> dict:
        """Reset SOUL.md to blank template."""
        SOUL_MD_PATH.write_text(_default_soul_md(), encoding="utf-8")
        self._answers = {}
        return {"status": "reset", "message": "SOUL.md אופס. הפעל do_start כדי להגדיר מחדש."}

    async def do_status(self) -> dict:
        """Check if SOUL.md has been filled."""
        content = _read_soul_md()
        filled = SOUL_MD_PATH.exists() and "[undefined]" not in content
        count = content.count("[undefined]")
        return {
            "filled": filled,
            "undefined_count": count,
            "message": "SOUL.md מוגדר ✓" if filled else f"SOUL.md חסר {count} הגדרות — הפעל do_start",
        }

    # ── Internal ──────────────────────────────────────────────────────

    async def _write_soul_md(self) -> dict:
        a = self._answers

        def val(key: str, default: str = "[undefined]") -> str:
            return a.get(key, default)

        name = val("name", "Jarvis")

        content = f"""# SOUL.md — Who I Am

> Auto-configured on {_today()}.
> To reconfigure: "הגדר מחדש את האישיות" or run soul_setup → do_reset then do_start.

## Identity

- **Name:** {name}
- **Inspired by:** {val("inspired_by")}
- **Primary language:** {val("primary_language")}

## Personality

- **Tone:** {val("tone")}
- **Honesty:** {val("honesty")}
- **MVP approach:** {val("mvp_mode")}
- **Extra:** {val("extra")}

## Communication Style

- **Verbosity:** {val("verbosity")}
- **Interruptions:** {val("interruptions")}

## Hard Rules

{val("hard_rules")}

## Working Style

- **Proactivity:** {val("proactivity")}
- Execute with good defaults. Update on progress. Only interrupt for blockers or when done.
"""

        SOUL_MD_PATH.write_text(content, encoding="utf-8")
        logger.info("SOUL.md written with %d fields", len(self._answers))

        return {
            "status": "done",
            "fields_filled": len(self._answers),
            "message": (
                f"✅ SOUL.md הוגדר! {name} מוכן עם {len(self._answers)}/{len(QUESTIONS)} הגדרות. "
                f"אפשר לשנות כל שדה בכל זמן."
            ),
        }


# ── Helpers ───────────────────────────────────────────────────────────

def _format_options(options: list[str] | None) -> str:
    if not options:
        return ""
    lines = "\n".join(f"  {i+1}. {o}" for i, o in enumerate(options))
    return f"\n\n*אפשרויות (כתוב מספר או כל תשובה חופשית):*\n{lines}"


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def _default_soul_md() -> str:
    if SOUL_TEMPLATE_PATH.exists():
        return SOUL_TEMPLATE_PATH.read_text(encoding="utf-8")
    return _blank_template()


def _read_soul_md() -> str:
    if SOUL_MD_PATH.exists():
        return SOUL_MD_PATH.read_text(encoding="utf-8")
    return _default_soul_md()


def _blank_template() -> str:
    return """# SOUL.md — Who I Am

> This file is filled automatically during assistant setup.
> Run: "הגדר את האישיות שלך" or trigger the soul_setup skill.

## Identity

- **Name:** [undefined]
- **Inspired by:** [undefined]
- **Primary language:** [undefined]

## Personality

[undefined]

## Communication Style

[undefined]

## Hard Rules

[undefined]

## Working Style

[undefined]
"""
