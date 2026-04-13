"""Startup check: Jarvis Ollama URL vs models actually on that server (avoids WSL/Windows mix-ups)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _model_names_from_list_response(resp) -> list[str]:
    names: list[str] = []
    for m in getattr(resp, "models", None) or []:
        if hasattr(m, "model") and m.model:
            names.append(m.model)
        elif isinstance(m, dict) and m.get("model"):
            names.append(m["model"])
    return names


async def warn_if_ollama_models_missing(settings) -> None:
    """If LLM provider is ollama, verify chat + embedding models exist on JARVIS_OLLAMA_HOST."""
    if getattr(settings, "llm_provider", "ollama").lower() not in ("ollama", ""):
        return

    host = settings.ollama_host
    try:
        import ollama

        client = ollama.AsyncClient(host=host)
        resp = await client.list()
        available = set(_model_names_from_list_response(resp))
    except Exception as e:
        print(
            f"\033[91m  Ollama: לא מצליח להתחבר ל־{host}\033[0m\n"
            f"\033[90m  ({e})\033[0m\n"
            f"\033[90m  ודא ש־Ollama רץ (אייקון במגש) וש־JARVIS_OLLAMA_HOST ב־.env נכון.\033[0m\n"
        )
        logger.warning("Ollama list failed at %s: %s", host, e)
        return

    if not available:
        print(
            f"\033[91m  Ollama ב־{host} עונה, אבל אין בו אף מודל (רשימה ריקה).\033[0m\n"
            f"\033[90m  אם ב־PowerShell רואים מודלים ב־`ollama list` — ייתכן שזה Ollama אחר (למשל WSL מול Windows).\033[0m\n"
            f"\033[90m  משוך כאן: ollama pull qwen3-vl:8b   ו־   ollama pull nomic-embed-text\033[0m\n"
        )
        return

    chat = settings.ollama_model
    embed = settings.embedding_model

    def _has(name: str) -> bool:
        if name in available:
            return True
        # Ollama sometimes omits :latest in list
        base = name.split(":")[0]
        return any(x == base or x.startswith(base + ":") for x in available)

    problems: list[str] = []
    if not _has(chat):
        problems.append(f"צ'אט: {chat!r} לא מופיע בשרת הזה")
    if embed and not _has(embed):
        problems.append(f"אמבדינג: {embed!r} לא מופיע בשרת הזה")

    if not problems:
        logger.info("Ollama OK at %s — %d model(s) listed", host, len(available))
        return

    sample = ", ".join(sorted(available)[:8])
    if len(available) > 8:
        sample += ", …"

    print(
        f"\033[91m  אזהרת Ollama — כתובת Jarvis: {host}\033[0m\n"
        f"\033[90m  " + "\n  ".join(problems) + "\033[0m\n"
        f"\033[90m  מודלים שכן רואים בכתובת הזו: {sample}\033[0m\n"
        f"\033[93m  פתרון נפוץ:\033[0m אותה מכונה עם שני Ollama (Windows + WSL). "
        f"ה־CLI שלך יכול להצביע על אחד, ו־Python על השני.\n"
        f"\033[90m  • משוך מודלים ב־PowerShell (Ollama של Windows), או\033[0m\n"
        f"\033[90m  • שנה JARVIS_OLLAMA_HOST ל־http://127.0.0.1:11434 או לכתובת השרת הנכון.\033[0m\n"
    )
    logger.warning("Ollama model mismatch at %s: %s | available=%s", host, problems, available)
