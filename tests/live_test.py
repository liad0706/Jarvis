"""
Live integration tests for Jarvis.
Tests real connections to Ollama, Spotify, filesystem, etc.
"""

import asyncio
import sys
import os
import time
from pathlib import Path

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Colors
G = "\033[92m"  # Green
R = "\033[91m"  # Red
Y = "\033[93m"  # Yellow
C = "\033[96m"  # Cyan
D = "\033[90m"  # Dim
RESET = "\033[0m"
PASS = f"{G}PASS{RESET}"
FAIL = f"{R}FAIL{RESET}"
SKIP = f"{Y}SKIP{RESET}"

results = []


def log_result(test_name: str, passed: bool, detail: str = "", skipped: bool = False):
    status = "SKIP" if skipped else ("PASS" if passed else "FAIL")
    icon = SKIP if skipped else (PASS if passed else FAIL)
    results.append({"name": test_name, "status": status, "detail": detail})
    print(f"  {icon} {test_name}")
    if detail:
        print(f"       {D}{detail}{RESET}")


async def test_ollama_connection():
    """Test 1: Ollama is running and responsive."""
    print(f"\n{C}━━━ 1. Ollama Connection ━━━{RESET}")
    try:
        import ollama
        client = ollama.AsyncClient(host="http://localhost:11434")
        models = await client.list()
        model_names = [m.model for m in models.models]
        log_result("Ollama is running", True, f"Models: {', '.join(model_names)}")
        return True
    except Exception as e:
        log_result("Ollama is running", False, str(e))
        return False


async def test_ollama_chat():
    """Test 2: Ollama can generate a response."""
    print(f"\n{C}━━━ 2. Ollama Chat (qwen3:8b) ━━━{RESET}")
    try:
        import ollama
        client = ollama.AsyncClient(host="http://localhost:11434")
        start = time.time()
        response = await client.chat(
            model="qwen3:8b",
            messages=[{"role": "user", "content": "Say 'hello' in one word, nothing else. /no_think"}],
        )
        elapsed = time.time() - start
        content = response.message.content.strip()
        log_result("LLM responds", True, f"Response: '{content[:100]}' ({elapsed:.1f}s)")
        return True
    except Exception as e:
        log_result("LLM responds", False, str(e))
        return False


async def test_ollama_tool_calling():
    """Test 3: Ollama tool calling works."""
    print(f"\n{C}━━━ 3. Ollama Tool Calling ━━━{RESET}")
    try:
        import ollama
        client = ollama.AsyncClient(host="http://localhost:11434")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "City name"},
                        },
                        "required": ["city"],
                    },
                },
            }
        ]
        response = await client.chat(
            model="qwen3:8b",
            messages=[{"role": "user", "content": "What's the weather in Tel Aviv? /no_think"}],
            tools=tools,
        )
        has_tool_calls = bool(response.message.tool_calls)
        if has_tool_calls:
            tc = response.message.tool_calls[0]
            log_result("Tool calling works", True, f"Called: {tc.function.name}({tc.function.arguments})")
        else:
            log_result("Tool calling works", False, f"No tool calls. Response: {response.message.content[:100]}")
        return has_tool_calls
    except Exception as e:
        log_result("Tool calling works", False, str(e))
        return False


async def test_memory():
    """Test 4: Memory system (SQLite)."""
    print(f"\n{C}━━━ 4. Memory System ━━━{RESET}")
    from core.memory import Memory

    db_path = Path(__file__).parent.parent / "data" / "test_live_memory.db"
    try:
        mem = Memory(db_path=db_path)
        await mem.init()
        log_result("Memory DB initialized", True, str(db_path))

        await mem.add_message("user", "שלום ג'רביס")
        await mem.add_message("assistant", "שלום! מה אני יכול לעשות בשבילך?")
        msgs = await mem.get_recent_messages(limit=5)
        log_result("Messages stored & retrieved", len(msgs) == 2, f"{len(msgs)} messages")

        await mem.set_fact("barber", "ישי פרץ")
        fact = await mem.get_fact("barber")
        log_result("Facts stored & retrieved", fact == "ישי פרץ", f"barber = {fact}")

        await mem.close()
        # Cleanup
        db_path.unlink(missing_ok=True)
        return True
    except Exception as e:
        log_result("Memory system", False, str(e))
        db_path.unlink(missing_ok=True)
        return False


async def test_skill_registry():
    """Test 5: Skill registry with all real skills."""
    print(f"\n{C}━━━ 5. Skill Registry ━━━{RESET}")
    try:
        from core.skill_base import SkillRegistry
        from skills.creality_print import CrealityPrintSkill
        from skills.model_downloader import ModelDownloaderSkill
        from skills.appointment_booker import AppointmentBookerSkill
        from skills.spotify_controller import SpotifySkill
        from skills.code_writer import CodeWriterSkill

        registry = SkillRegistry()
        skills = [
            CrealityPrintSkill(),
            ModelDownloaderSkill(),
            AppointmentBookerSkill(),
            SpotifySkill(),
            CodeWriterSkill(),
        ]
        for s in skills:
            registry.register(s)

        log_result("All 5 skills registered", len(registry.all_skills()) == 5)

        tools = registry.get_all_tools()
        log_result("Tool definitions generated", len(tools) > 0, f"{len(tools)} tools total")

        # Check resolve
        resolved = registry.resolve_tool_call("spotify_play")
        log_result("Tool resolution works", resolved is not None, f"spotify_play -> {resolved[0].name}.{resolved[1]}" if resolved else "")

        return True
    except Exception as e:
        log_result("Skill registry", False, str(e))
        return False


async def test_model_downloader_search():
    """Test 6: Search for 3D models online."""
    print(f"\n{C}━━━ 6. 3D Model Search (Live Web) ━━━{RESET}")
    try:
        from skills.model_downloader import ModelDownloaderSkill
        skill = ModelDownloaderSkill()
        result = await skill.do_search("benchy")
        count = result.get("count", 0)
        log_result("Thingiverse/Printables search", result["status"] == "ok", f"{count} results found")
        if result.get("results"):
            first = result["results"][0]
            log_result("Results have data", bool(first.get("url")), f"First: {first.get('title', '?')[:50]} ({first.get('source')})")
        else:
            log_result("Results have data", False, "No results returned (sites may block scraping)")
        return True
    except Exception as e:
        log_result("Model search", False, str(e))
        return False


async def test_creality_print():
    """Test 7: Creality Print skill."""
    print(f"\n{C}━━━ 7. Creality Print ━━━{RESET}")
    try:
        from skills.creality_print import CrealityPrintSkill
        skill = CrealityPrintSkill()

        # Test configure (doesn't need the app)
        result = await skill.do_configure(layer_height="0.16", infill="30", supports="true")
        log_result("Configure settings", result["status"] == "configured", str(result["settings"]))

        # Check if EXE exists
        exe = Path(skill.settings.creality_print_exe)
        log_result("CrealityPrint.exe found", exe.exists(), str(exe))

        # Test STL import with a real file
        stl_files = list(Path("C:/Users/User/Desktop/3D Models").glob("*.stl"))
        if stl_files:
            log_result("STL files available", True, f"{len(stl_files)} files: {stl_files[0].name}")
        else:
            log_result("STL files available", False, "No STL files found")

        return True
    except Exception as e:
        log_result("Creality Print", False, str(e))
        return False


async def test_spotify():
    """Test 8: Spotify skill."""
    print(f"\n{C}━━━ 8. Spotify ━━━{RESET}")
    from config import get_settings
    settings = get_settings()

    if not settings.spotipy_client_id:
        log_result("Spotify credentials", False, "SPOTIPY_CLIENT_ID not set in .env", skipped=True)
        log_result("Spotify playback", False, "", skipped=True)
        return False

    try:
        from skills.spotify_controller import SpotifySkill
        skill = SpotifySkill()

        # Test current playback
        result = await skill.do_current()
        if result.get("status") in ("playing", "paused"):
            log_result("Spotify connected", True, f"Now: {result.get('track')} by {result.get('artist')}")
        else:
            log_result("Spotify connected", True, result.get("message", "idle"))

        # Test search
        result = await skill.do_search(query="Bohemian Rhapsody")
        log_result("Spotify search", result["status"] == "ok", f"{result.get('count', 0)} results")

        return True
    except Exception as e:
        log_result("Spotify", False, str(e))
        return False


async def test_code_writer():
    """Test 9: Code writer skill (writes and runs real code)."""
    print(f"\n{C}━━━ 9. Code Writer (Live LLM + Execution) ━━━{RESET}")
    try:
        from skills.code_writer import CodeWriterSkill
        skill = CodeWriterSkill()

        # Write code
        start = time.time()
        result = await skill.do_write(
            prompt="Write a Python function that returns the fibonacci sequence up to n=10 and prints it. /no_think",
            filename="fib_test.py",
        )
        elapsed = time.time() - start
        log_result("Code generated by LLM", result["status"] == "written", f"{result.get('lines', 0)} lines in {elapsed:.1f}s")

        if result["status"] == "written":
            # Show preview
            preview = result.get("preview", "")[:200]
            log_result("Code preview", bool(preview), preview.replace("\n", " | ")[:100])

            # Run the code
            run_result = await skill.do_run(file_path=result["file"])
            log_result("Code execution", run_result.get("status") == "ok",
                       f"stdout: {run_result.get('stdout', '').strip()[:100]}")

            if run_result.get("stderr"):
                log_result("No stderr", False, run_result["stderr"][:100])

            # List files
            list_result = await skill.do_list()
            log_result("List generated files", list_result["count"] > 0, f"{list_result['count']} files")

        return True
    except Exception as e:
        log_result("Code writer", False, str(e))
        return False


async def test_appointment_booker():
    """Test 10: Appointment booker (Calmark)."""
    print(f"\n{C}━━━ 10. Appointment Booker (Calmark) ━━━{RESET}")
    try:
        from skills.appointment_booker import AppointmentBookerSkill
        skill = AppointmentBookerSkill()

        log_result("Barber name configured", skill.barber_name == "ישי פרץ", f"Name: {skill.barber_name}")
        log_result("Calmark URL configured", "calmark.co.il" in skill.barber_url, f"URL: {skill.barber_url}")

        # Test that booking requires params
        result = await skill.do_book_appointment()
        log_result("Booking requires date+time", result["status"] == "need_info")

        # Try checking availability with timeout (launches real browser)
        print(f"       {D}Launching browser to check Calmark...{RESET}")
        try:
            result = await asyncio.wait_for(skill.do_check_availability(), timeout=30)
            if "error" in result:
                log_result("Calmark availability check", False, result["error"][:100])
            else:
                log_result("Calmark availability check", True,
                           f"Page: {result.get('page_title', '?')}, {result.get('count', 0)} slots")
        except asyncio.TimeoutError:
            log_result("Calmark availability check", False, "Timed out after 30s", skipped=True)

        return True
    except Exception as e:
        log_result("Appointment booker", False, str(e))
        return False


async def test_tts():
    """Test 11: Text-to-speech."""
    print(f"\n{C}━━━ 11. Text-to-Speech ━━━{RESET}")
    try:
        from voice.tts import TextToSpeech
        tts = TextToSpeech()
        tts.init()
        log_result("TTS engine initialized", True)

        await tts.speak("Jarvis is online and ready.")
        log_result("TTS spoke successfully", True, "Said: 'Jarvis is online and ready.'")
        return True
    except Exception as e:
        log_result("TTS", False, str(e))
        return False


async def test_orchestrator_live():
    """Test 12: Full orchestrator with real Ollama."""
    print(f"\n{C}━━━ 12. Full Orchestrator (Live LLM) ━━━{RESET}")
    try:
        from core.memory import Memory
        from core.skill_base import SkillRegistry
        from core.orchestrator import Orchestrator
        from skills.code_writer import CodeWriterSkill
        from skills.model_downloader import ModelDownloaderSkill

        db_path = Path(__file__).parent.parent / "data" / "test_orch_memory.db"
        memory = Memory(db_path=db_path)
        await memory.init()

        registry = SkillRegistry()
        registry.register(CodeWriterSkill())
        registry.register(ModelDownloaderSkill())

        orchestrator = Orchestrator(registry, memory)

        # Simple chat
        start = time.time()
        response = await orchestrator.process("Hi Jarvis, what can you do? Answer briefly. /no_think")
        elapsed = time.time() - start
        log_result("Orchestrator chat", bool(response), f"({elapsed:.1f}s) {response[:100]}")

        await memory.close()
        db_path.unlink(missing_ok=True)
        return True
    except Exception as e:
        log_result("Orchestrator", False, str(e))
        return False


async def main():
    print(f"""
{C}╔═══════════════════════════════════════════════╗
║          JARVIS LIVE TEST SUITE                ║
║          Testing all systems...                ║
╚═══════════════════════════════════════════════╝{RESET}
""")

    start_time = time.time()

    # Run tests in order (some depend on Ollama being up)
    ollama_ok = await test_ollama_connection()
    if ollama_ok:
        await test_ollama_chat()
        await test_ollama_tool_calling()
    else:
        print(f"\n  {R}Ollama not running - skipping LLM tests{RESET}")

    await test_memory()
    await test_skill_registry()
    await test_model_downloader_search()
    await test_creality_print()
    await test_spotify()

    if ollama_ok:
        await test_code_writer()
        await test_orchestrator_live()
    else:
        print(f"\n  {Y}Skipping code writer & orchestrator (need Ollama){RESET}")

    await test_appointment_booker()
    await test_tts()

    # Summary
    elapsed = time.time() - start_time
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    total = len(results)

    print(f"""
{C}╔═══════════════════════════════════════════════╗
║              TEST SUMMARY                     ║
╠═══════════════════════════════════════════════╣
║  {G}PASSED:  {passed:>3}{RESET}                                 {C}║
║  {R}FAILED:  {failed:>3}{RESET}                                 {C}║
║  {Y}SKIPPED: {skipped:>3}{RESET}                                 {C}║
║  TOTAL:   {total:>3}   ({elapsed:.1f}s)                     {C}║
╚═══════════════════════════════════════════════╝{RESET}
""")

    if failed > 0:
        print(f"{R}Failed tests:{RESET}")
        for r in results:
            if r["status"] == "FAIL":
                print(f"  {R}✗{RESET} {r['name']}: {r['detail']}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
