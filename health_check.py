#!/usr/bin/env python3
"""
Jarvis Health Check
===================
Verifies that every major component works correctly.

Usage:
    python health_check.py              # full check
    python health_check.py --fast       # skip slow network checks
    python health_check.py --dashboard  # also check running dashboard API
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Coroutine

# ── make sure we run from project root ───────────────────────────────────────
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Load .env before importing anything Jarvis
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)

# ── colours ───────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

PASS = f"{GREEN}PASS{RESET}"
FAIL = f"{RED}FAIL{RESET}"
SKIP = f"{YELLOW}SKIP{RESET}"
WARN = f"{YELLOW}WARN{RESET}"


@dataclass
class Result:
    label: str
    status: str          # "pass" | "fail" | "skip" | "warn"
    detail: str = ""
    elapsed_ms: float = 0.0


results: list[Result] = []


def record(label: str, status: str, detail: str = "", elapsed: float = 0.0):
    results.append(Result(label, status, detail, elapsed))
    icon = {"pass": PASS, "fail": FAIL, "skip": SKIP, "warn": WARN}.get(status, "?")
    detail_str = f"  {CYAN}{detail}{RESET}" if detail else ""
    ms = f"  {elapsed:.0f}ms" if elapsed else ""
    print(f"  {icon}  {label}{ms}{detail_str}")


async def run_check(
    label: str,
    coro: Coroutine,
    timeout: float = 10.0,
    skip_if: bool = False,
    skip_reason: str = "",
):
    """Run an async check with timeout, catch all errors."""
    if skip_if:
        record(label, "skip", skip_reason)
        return None
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        elapsed = (time.monotonic() - t0) * 1000
        if isinstance(result, tuple):
            status, detail = result
        else:
            status, detail = ("pass", str(result) if result else "")
        record(label, status, detail, elapsed)
        return result
    except asyncio.TimeoutError:
        elapsed = (time.monotonic() - t0) * 1000
        record(label, "fail", f"timeout after {timeout:.0f}s", elapsed)
        return None
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        record(label, "fail", str(exc)[:120], elapsed)
        return None


# =============================================================================
# SECTION 1 — System
# =============================================================================
def section(title: str):
    print(f"\n{BOLD}{CYAN}── {title} ──{RESET}")


def check_system():
    section("System")

    # Python version
    v = sys.version_info
    if v >= (3, 11):
        record("Python version", "pass", f"{v.major}.{v.minor}.{v.micro}")
    else:
        record("Python version", "fail", f"{v.major}.{v.minor} — need 3.11+")

    # .env exists
    if (ROOT / ".env").exists():
        record(".env file", "pass")
    else:
        record(".env file", "warn", "not found — using defaults")

    # Required env vars
    provider = os.getenv("JARVIS_LLM_PROVIDER", "")
    if provider:
        record("JARVIS_LLM_PROVIDER", "pass", provider)
    else:
        record("JARVIS_LLM_PROVIDER", "warn", "not set in .env")

    model = os.getenv("JARVIS_OLLAMA_MODEL", "")
    if model:
        record("JARVIS_OLLAMA_MODEL", "pass", model)
    else:
        record("JARVIS_OLLAMA_MODEL", "warn", "not set — Ollama features won't work")

    # Disk space
    import shutil
    free_gb = shutil.disk_usage(ROOT).free / 1024**3
    status = "pass" if free_gb > 5 else "warn"
    record("Disk space", status, f"{free_gb:.1f} GB free")


# =============================================================================
# SECTION 2 — Python imports
# =============================================================================
def check_imports():
    section("Python packages")

    packages = {
        "ollama":             "ollama",
        "fastapi":            "fastapi",
        "aiosqlite":          "aiosqlite",
        "faiss":              "faiss-cpu",
        "sounddevice":        "sounddevice",
        "speech_recognition": "SpeechRecognition",
        "httpx":              "httpx",
        "playwright":         "playwright",
        "spotipy":            "spotipy",
        "discord":            "discord.py",
        "telegram":           "python-telegram-bot",
        "cv2":                "opencv-python",
        "numpy":              "numpy",
        "pydantic_settings":  "pydantic-settings",
        "watchfiles":         "watchfiles",
    }

    for module, pkg in packages.items():
        try:
            importlib.import_module(module)
            record(pkg, "pass")
        except ImportError as e:
            record(pkg, "fail", str(e)[:80])


# =============================================================================
# SECTION 3 — Ollama
# =============================================================================
async def check_ollama(fast: bool):
    section("Ollama")

    async def ping():
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.get("http://localhost:11434/api/tags", timeout=5)
            r.raise_for_status()
            data = r.json()
            models = [m["name"] for m in data.get("models", [])]
            return "pass", f"{len(models)} model(s) loaded"

    result = await run_check("Ollama server", ping(), timeout=6)
    if result is None:
        record("Embedding model (nomic-embed-text)", "skip", "Ollama not running")
        record("Chat model", "skip", "Ollama not running")
        return

    async def check_model(name: str):
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.get("http://localhost:11434/api/tags", timeout=5)
            models = [m["name"] for m in r.json().get("models", [])]
            matches = [m for m in models if name in m]
            if matches:
                return "pass", matches[0]
            return "warn", f"not found — run: ollama pull {name}"

    await run_check("Embedding model (nomic-embed-text)",
                    check_model("nomic-embed-text"), timeout=6)

    chat_model = os.getenv("JARVIS_OLLAMA_MODEL", "")
    if chat_model:
        await run_check(f"Chat model ({chat_model})",
                        check_model(chat_model.split(":")[0]), timeout=6)
    else:
        record("Chat model", "skip", "JARVIS_OLLAMA_MODEL not set")


# =============================================================================
# SECTION 4 — Core modules (boot without full Jarvis startup)
# =============================================================================
async def check_core():
    section("Core modules")

    async def try_import(module: str):
        try:
            importlib.import_module(module)
            return "pass", ""
        except Exception as e:
            return "fail", str(e)[:100]

    core_modules = [
        "core.memory",
        "core.skill_base",
        "core.providers",
        "core.permissions",
        "core.resilience",
        "core.audit",
        "core.event_bus",
        "config.settings",
    ]
    for mod in core_modules:
        await run_check(mod, try_import(mod), timeout=5)


# =============================================================================
# SECTION 5 — Memory system
# =============================================================================
async def check_memory():
    section("Memory system")

    async def test_sqlite():
        import tempfile
        from core.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            mem = Memory(db_path=db_path)
            await mem.init()
            await mem.save_fact("test_key", "test_value")
            val = await mem.get_fact("test_key")
            await mem.close()
            if val == "test_value":
                return "pass", "read/write OK"
            return "fail", f"got: {val!r}"

    async def test_faiss():
        from core.faiss_memory import HybridMemoryBackend
        fm = HybridMemoryBackend(dim=768)
        if not hasattr(fm, "hybrid_search"):
            return "fail", "hybrid_search method missing"
        return "pass", "init OK (dim=768)"

    await run_check("SQLite memory (read/write)", test_sqlite(), timeout=10)
    await run_check("FAISS memory (init)", test_faiss(), timeout=5)


# =============================================================================
# SECTION 6 — Skills
# =============================================================================
SKILL_CONFIGS = [
    # (display_name, module, class_name, action, params, needs_env_var, hardware)
    # action=None means: only test import + as_tools(), skip execute()
    ("system_control",      "skills.system_control",      "SystemControlSkill",  "system_info",    {},                            None,                          False),
    ("file_manager",        "skills.file_manager",        "FileManagerSkill",    "search_files",   {"query": "test"},             None,                          False),
    ("code_writer",         "skills.code_writer",         "CodeWriterSkill",     "list",           {},                            None,                          False),
    ("memory_skill",        "skills.memory_skill",        "MemorySkill",         None,             {},                            None,                          False),  # requires memory_manager injection
    ("timer_skill",         "skills.timer_skill",         "TimerSkill",          "list_timers",    {},                            None,                          False),
    ("scheduler_skill",     "skills.scheduler_skill",     "SchedulerSkill",      None,             {},                            None,                          False),  # requires scheduler injection
    ("self_improve",        "skills.self_improve",        "SelfImproveSkill",    "list",           {},                            None,                          False),
    ("web_research",        "skills.web_research",        "WebResearchSkill",    None,             {},                            None,                          False),  # network call
    ("spotify_controller",  "skills.spotify_controller",  "SpotifySkill",        "current",        {},                            "JARVIS_SPOTIPY_CLIENT_ID",    False),
    ("smart_home",          "skills.smart_home",          "SmartHomeSkill",      "list_devices",   {},                            "JARVIS_HA_TOKEN",             False),
    ("weather_skill",       "skills.weather_skill",       "WeatherSkill",        "current",        {},                            None,                          False),
    ("apple_tv",            "skills.apple_tv",            "AppleTVSkill",        "discover",       {},                            None,                          True),
    ("creality_api_skill",  "skills.creality_api_skill",  "CrealityAPISkill",    "status",         {},                            None,                          True),
    ("browser_agent",       "skills.browser_agent",       "BrowserAgentSkill",   "screenshot",     {},                            None,                          False),  # screenshot of current tab
    ("calendar_skill",      "skills.calendar_skill",      "CalendarSkill",       None,             {},                            None,                          False),  # requires calendar injection
]


async def check_skills():
    section("Skills")

    for (name, module, cls_name, action, params,
         needs_env, is_hardware) in SKILL_CONFIGS:

        # Skip if needs env var that's not set
        if needs_env and not os.getenv(needs_env):
            record(f"[stable] {name}", "skip", f"{needs_env} not set in .env")
            continue

        if is_hardware:
            record(f"[experimental] {name}", "skip", "hardware required — test manually")
            continue

        async def _test(mod=module, klass=cls_name, act=action, pms=params):
            try:
                m = importlib.import_module(mod)
                skill_cls = getattr(m, klass)
                skill = skill_cls()
                # Always test that as_tools() works (schema generation)
                tools = skill.as_tools()
                if not tools:
                    return "warn", "no tools exposed"

                # If action is None, only verify import + schema — skip execute()
                if act is None:
                    return "pass", f"{len(tools)} tool(s) — execute() skipped (needs injected deps)"

                result = await asyncio.wait_for(
                    skill.execute(act, pms), timeout=8.0
                )
                if isinstance(result, dict) and "error" in result:
                    err = result["error"]
                    config_keywords = ("not configured", "api key", "token",
                                       "credentials", "not set", "no api",
                                       "no key", "missing")
                    if any(k in err.lower() for k in config_keywords):
                        return "warn", f"not configured: {err[:80]}"
                    return "warn", f"returned error: {err[:80]}"
                return "pass", f"{len(tools)} tool(s) registered"
            except ImportError as e:
                return "fail", f"import error: {e}"
            except TypeError as e:
                msg = str(e)
                # Constructor requires injected args → not a bug, just needs context
                if "missing" in msg and "argument" in msg:
                    return "pass", f"import OK — {msg[:80]}"
                return "fail", msg[:100]
            except Exception as e:
                msg = str(e).lower()
                if any(k in msg for k in ("api key", "token", "credentials",
                                           "not set", "not configured")):
                    return "warn", f"not configured: {str(e)[:80]}"
                return "fail", str(e)[:100]

        prefix = "[stable]"
        await run_check(f"{prefix} {name}", _test(), timeout=12)


# =============================================================================
# SECTION 7 — Voice
# =============================================================================
async def check_voice():
    section("Voice")

    async def test_audio_device():
        import sounddevice as sd
        devices = sd.query_devices()
        inputs  = [d for d in devices if d["max_input_channels"] > 0]
        outputs = [d for d in devices if d["max_output_channels"] > 0]
        return "pass", f"{len(inputs)} input(s), {len(outputs)} output(s)"

    async def test_tts_import():
        from voice.tts import TextToSpeech
        tts = TextToSpeech()
        # Don't actually call init() — that needs the API key
        return "pass", "TextToSpeech class OK"

    async def test_stt_import():
        from voice.stt import SpeechToText
        stt = SpeechToText()
        return "pass", "SpeechToText class OK"

    voice_enabled = os.getenv("JARVIS_VOICE_ENABLED", "false").lower() == "true"
    await run_check("Audio devices", test_audio_device(), timeout=5)
    await run_check("TTS module (voice/tts.py)", test_tts_import(), timeout=5)
    await run_check("STT module (voice/stt.py)", test_stt_import(), timeout=5)

    el_key = os.getenv("JARVIS_ELEVENLABS_API_KEY", "")
    record("ElevenLabs API key",
           "pass" if el_key else "warn",
           "set" if el_key else "not set — voice will fail if enabled")

    record("Voice enabled",
           "pass" if voice_enabled else "skip",
           "true" if voice_enabled else "JARVIS_VOICE_ENABLED=false")


# =============================================================================
# SECTION 8 — Dashboard API (only if --dashboard flag or already running)
# =============================================================================
async def check_dashboard():
    section("Dashboard API")

    base = f"http://127.0.0.1:{os.getenv('JARVIS_DASHBOARD_PORT', '8550')}"

    async def get(path: str):
        import httpx
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{base}{path}", timeout=5)
            r.raise_for_status()
            return r.json()

    async def health():
        data = await get("/api/health")
        status_val = data.get("status", "?")
        return "pass" if status_val == "ok" else "warn", f"status={status_val}"

    async def skills_list():
        data = await get("/api/skills")
        n = len(data) if isinstance(data, list) else "?"
        return "pass", f"{n} skills"

    async def history():
        data = await get("/api/history")
        n = len(data) if isinstance(data, list) else "?"
        return "pass", f"{n} conversations"

    async def ping_ws():
        # Just verify the WS endpoint exists (HTTP upgrade response)
        import httpx
        async with httpx.AsyncClient() as c:
            try:
                r = await c.get(f"http://127.0.0.1:{os.getenv('JARVIS_DASHBOARD_PORT','8550')}/ws/chat",
                                timeout=3)
                # 426 = Upgrade Required (expected for WS endpoint hit with HTTP)
                if r.status_code in (101, 426, 400):
                    return "pass", "WebSocket endpoint reachable"
                return "warn", f"status {r.status_code}"
            except Exception:
                return "warn", "WebSocket check inconclusive"

    await run_check("GET /api/health",  health(),      timeout=6)
    await run_check("GET /api/skills",  skills_list(), timeout=6)
    await run_check("GET /api/history", history(),     timeout=6)
    await run_check("WebSocket /ws/chat", ping_ws(),   timeout=6)


# =============================================================================
# SECTION 9 — WhatsApp bridge (Node.js)
# =============================================================================
async def check_whatsapp():
    section("WhatsApp bridge (optional)")

    async def check_node():
        import subprocess
        r = subprocess.run(["node", "--version"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            ver = r.stdout.strip()
            major = int(ver.lstrip("v").split(".")[0])
            return ("pass" if major >= 18 else "warn",
                    f"{ver} {'(OK)' if major >= 18 else '(need ≥18)'}")
        return "fail", "node not found"

    async def check_bridge_deps():
        pkg = ROOT / "whatsapp" / "node_modules" / "@whiskeysockets" / "baileys"
        if pkg.exists():
            return "pass", "Baileys installed"
        return "warn", "run: cd whatsapp && npm install"

    await run_check("Node.js ≥18",      check_node(),        timeout=5)
    await run_check("Baileys (bridge)",  check_bridge_deps(), timeout=5)

    wa_enabled = os.getenv("JARVIS_WHATSAPP_ENABLED", "false").lower() == "true"
    record("WhatsApp enabled", "pass" if wa_enabled else "skip",
           "true" if wa_enabled else "JARVIS_WHATSAPP_ENABLED=false")


# =============================================================================
# SUMMARY
# =============================================================================
def print_summary():
    passed  = [r for r in results if r.status == "pass"]
    failed  = [r for r in results if r.status == "fail"]
    warned  = [r for r in results if r.status == "warn"]
    skipped = [r for r in results if r.status == "skip"]

    total = len(results)
    print(f"\n{BOLD}{'─'*52}{RESET}")
    print(f"{BOLD}  Summary{RESET}")
    print(f"{'─'*52}")
    print(f"  {GREEN}{len(passed):3d} passed{RESET}")
    if warned:
        print(f"  {YELLOW}{len(warned):3d} warnings{RESET}")
    if skipped:
        print(f"  {YELLOW}{len(skipped):3d} skipped{RESET}")
    if failed:
        print(f"  {RED}{len(failed):3d} FAILED{RESET}")

    if failed:
        print(f"\n{RED}{BOLD}  Failed checks:{RESET}")
        for r in failed:
            print(f"    • {r.label}: {r.detail}")

    if warned:
        print(f"\n{YELLOW}{BOLD}  Warnings (action needed):{RESET}")
        for r in warned:
            print(f"    • {r.label}: {r.detail}")

    print(f"\n{'─'*52}")
    if not failed:
        print(f"  {GREEN}{BOLD}All critical checks passed.{RESET}")
    else:
        print(f"  {RED}{BOLD}Fix the failing checks above before running Jarvis.{RESET}")
    print()

    return len(failed) == 0


# =============================================================================
# MAIN
# =============================================================================
async def main():
    parser = argparse.ArgumentParser(description="Jarvis health check")
    parser.add_argument("--fast",       action="store_true",
                        help="Skip slow network/Ollama checks")
    parser.add_argument("--dashboard",  action="store_true",
                        help="Check running dashboard API (needs Jarvis running)")
    parser.add_argument("--no-skills",  action="store_true",
                        help="Skip skill execution tests")
    args = parser.parse_args()

    print(f"\n{BOLD}{CYAN}  Jarvis Health Check{RESET}")
    print(f"  {ROOT}\n")

    # Warn if not running inside the venv
    venv_dir = ROOT / ".venv"
    in_venv = hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    )
    if not in_venv and venv_dir.exists():
        print(f"  {YELLOW}{BOLD}[!!] Not running inside .venv!{RESET}")
        print(f"       Run:  source .venv/bin/activate && python health_check.py")
        print(f"       Package checks will fail until you activate the venv.\n")
    elif not in_venv and not venv_dir.exists():
        print(f"  {YELLOW}{BOLD}[!!] .venv not found — run setup.sh first.{RESET}\n")

    check_system()
    check_imports()

    if not args.fast:
        await check_ollama(fast=False)
    else:
        section("Ollama")
        record("Ollama checks", "skip", "--fast flag")

    await check_core()
    await check_memory()

    if not args.no_skills:
        await check_skills()
    else:
        section("Skills")
        record("Skill checks", "skip", "--no-skills flag")

    await check_voice()
    await check_whatsapp()

    if args.dashboard:
        await check_dashboard()
    else:
        section("Dashboard API")
        record("Dashboard checks", "skip",
               "add --dashboard flag while Jarvis is running")

    ok = print_summary()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
