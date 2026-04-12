"""Smart model router - picks the best LLM for each task type."""

from __future__ import annotations

import logging
import re

from config import get_settings

logger = logging.getLogger(__name__)


class TaskType:
    GREETING = "greeting"
    TOOL_CALLING = "tool_calling"
    VISION = "vision"
    CODE = "code"
    RESEARCH = "research"
    GENERAL = "general"


class ModelRouter:
    """Routes tasks to the best available model based on task type."""

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self._routes: dict[str, dict] = {}
        self._configure_defaults()

    def _configure_defaults(self):
        """Set up default routing rules based on available providers."""
        default_provider = self.settings.llm_provider
        ollama_fallback = self._ollama_fallback_provider(default_provider)
        vision_provider, vision_model, vision_fallback = self._vision_route(default_provider)

        self._routes = {
            TaskType.GREETING: {
                "preferred_provider": default_provider,
                "preferred_model": None,
                "fallback_provider": ollama_fallback,
                "description": "Greetings via default provider",
            },
            TaskType.TOOL_CALLING: {
                "preferred_provider": default_provider,
                "preferred_model": None,
                "fallback_provider": ollama_fallback,
                "description": "Best tool-calling model",
            },
            TaskType.VISION: {
                "preferred_provider": vision_provider,
                "preferred_model": vision_model,
                "fallback_provider": vision_fallback,
                "description": "Vision-capable model",
            },
            TaskType.CODE: {
                "preferred_provider": "codex" if default_provider != "codex" else default_provider,
                "preferred_model": None,
                "fallback_provider": default_provider if default_provider != "codex" else "",
                "description": "Code generation model",
            },
            TaskType.RESEARCH: {
                "preferred_provider": default_provider,
                "preferred_model": None,
                "fallback_provider": ollama_fallback,
                "description": "Research and long-form synthesis",
            },
            TaskType.GENERAL: {
                "preferred_provider": default_provider,
                "preferred_model": None,
                "fallback_provider": ollama_fallback,
                "description": "General conversation",
            },
        }

    def _ollama_fallback_provider(self, default_provider: str) -> str:
        enabled = bool(getattr(self.settings, "ollama_fallback_enabled", True))
        if enabled and default_provider != "ollama":
            return "ollama"
        return ""

    def _vision_route(self, default_provider: str) -> tuple[str, str | None, str]:
        enabled = bool(getattr(self.settings, "ollama_fallback_enabled", True))
        if default_provider == "ollama":
            return "ollama", "qwen3-vl", ""
        if enabled:
            return default_provider, None, "ollama"
        return default_provider, None, ""

    def classify_task(
        self,
        user_input: str,
        has_images: bool = False,
        tool_names: list[str] | None = None,
    ) -> str:
        """Classify the task type from user input."""
        if has_images:
            return TaskType.VISION

        text = (user_input or "").strip().lower()

        greeting_re = re.compile(
            r"^\s*(hi|hello|hey|yo|hiya|sup|שלום|היי|מה\s*נשמע|מה\s*קורה|בוקר\s*טוב|ערב\s*טוב)\s*[!?.]*\s*$",
            re.IGNORECASE,
        )
        if greeting_re.match(text):
            return TaskType.GREETING

        if self._is_vision_capability_question(text):
            return TaskType.GENERAL

        if self._is_explicit_vision_request(text):
            return TaskType.VISION

        code_keywords = {
            "code", "script", "function", "class", "debug", "refactor", "bug",
            "קוד", "סקריפט", "פונקציה", "מחלקה", "דבאג", "רפקטור", "באג",
        }
        if any(keyword in text for keyword in code_keywords):
            return TaskType.CODE

        research_keywords = {
            "research", "search the web", "summarize article", "compare sources",
            "document", "documents", "pdf", "rag", "notes", "study material",
            "מחקר", "חפש ברשת", "סכם כתבה", "השווה מקורות", "מסמך", "מסמכים", "pdf",
        }
        if any(keyword in text for keyword in research_keywords):
            return TaskType.RESEARCH

        tool_keywords = {
            "turn on", "turn off", "toggle", "play", "pause", "resume",
            "calendar", "schedule", "appointment", "device", "light", "tv",
            "spotify", "file", "folder", "download", "remember", "automation",
            "הדלק", "כבה", "נגן", "עצור", "המשך", "יומן", "לוח זמנים", "תור",
            "מכשיר", "אור", "טלוויזיה", "ספוטיפיי", "קובץ", "תיקייה", "הורדות", "אוטומציה",
        }
        if tool_names and any(keyword in text for keyword in tool_keywords):
            return TaskType.TOOL_CALLING

        return TaskType.GENERAL

    @staticmethod
    def _is_vision_capability_question(text: str) -> bool:
        vision_terms = (
            "camera", "screen", "screenshot", "image", "vision", "ocr",
            "מצלמה", "מסך", "צילום מסך", "תמונה", "ראייה", "לראות",
        )
        capability_terms = (
            "what can you", "what do you know", "capabilities", "abilities",
            "what are your", "can you do", "able to",
            "מה אתה יכול", "מה את יכולה", "מה אתה יודע", "מה את יודעת",
            "יכולות", "יכולת", "מסוגל",
        )
        if not any(term in text for term in vision_terms):
            return False
        if any(term in text for term in capability_terms):
            return True
        patterns = (
            r"what\s+can\s+you.*(?:camera|screen|vision|image)",
            r"(?:camera|screen|vision|image).*(?:capabilities|abilities)",
            r"מה.*יכול.*(?:מצלמה|מסך|ראייה|תמונה)",
            r"(?:מצלמה|מסך|ראייה|תמונה).*(?:יכולות|מסוגל|יודע)",
        )
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)

    @staticmethod
    def _is_explicit_vision_request(text: str) -> bool:
        patterns = (
            r"what(?:'s| is)\s+on\s+(?:my|the)\s+screen",
            r"read(?: all)?\s+text\s+from\s+(?:the\s+)?screen",
            r"take\s+(?:a\s+)?screenshot",
            r"look\s+at\s+(?:my|the)\s+(?:screen|image|camera)",
            r"what\s+do\s+you\s+see",
            r"see\s+through\s+(?:my|the)\s+camera",
            r"analy[sz]e\s+(?:the\s+)?(?:image|photo|screen)",
            r"\bocr\b",
            r"מה\s+על\s+המסך",
            r"מה\s+מוצג\s+על\s+המסך",
            r"קרא.*טקסט.*מהמסך",
            r"צלם.*מסך",
            r"תסתכל.*(?:מסך|תמונה|מצלמה)",
            r"מה\s+אתה\s+רואה",
            r"מה\s+את\s+רואה",
            r"מה\s+אתה\s+רואה\s+דרך\s+המצלמה",
            r"מה\s+את\s+רואה\s+דרך\s+המצלמה",
        )
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)

    def get_route(self, task_type: str) -> dict:
        """Get the routing config for a task type."""
        return self._routes.get(task_type, self._routes[TaskType.GENERAL])

    def set_route(self, task_type: str, provider: str, model: str | None = None):
        """Override routing for a task type."""
        if task_type in self._routes:
            self._routes[task_type]["preferred_provider"] = provider
            if model is not None:
                self._routes[task_type]["preferred_model"] = model
            logger.info("Route updated: %s -> %s/%s", task_type, provider, model)

    def get_all_routes(self) -> dict[str, dict]:
        """Return all current routing rules."""
        return dict(self._routes)
