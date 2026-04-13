from types import SimpleNamespace

from core.model_router import ModelRouter, TaskType


def test_model_router_keeps_vision_capability_questions_as_general():
    router = ModelRouter(settings=SimpleNamespace(llm_provider="codex"))

    result = router.classify_task("מה אתה יכול לעשות עם המצלמה שלי ומה יכולות הראייה שלך?")

    assert result == TaskType.GENERAL


def test_model_router_marks_live_camera_request_as_vision():
    router = ModelRouter(settings=SimpleNamespace(llm_provider="codex"))

    result = router.classify_task("מה אתה רואה דרך המצלמה שלי עכשיו?")

    assert result == TaskType.VISION


def test_model_router_disables_ollama_fallback_when_flag_is_false():
    router = ModelRouter(
        settings=SimpleNamespace(llm_provider="codex", ollama_fallback_enabled=False)
    )

    general = router.get_route(TaskType.GENERAL)
    tool_calling = router.get_route(TaskType.TOOL_CALLING)
    vision = router.get_route(TaskType.VISION)

    assert general["fallback_provider"] == ""
    assert tool_calling["fallback_provider"] == ""
    assert vision["preferred_provider"] == "codex"
    assert vision["fallback_provider"] == ""


def test_model_router_keeps_optional_ollama_fallback_when_enabled():
    router = ModelRouter(
        settings=SimpleNamespace(llm_provider="codex", ollama_fallback_enabled=True)
    )

    general = router.get_route(TaskType.GENERAL)
    vision = router.get_route(TaskType.VISION)

    assert general["fallback_provider"] == "ollama"
    assert vision["preferred_provider"] == "codex"
    assert vision["fallback_provider"] == "ollama"
