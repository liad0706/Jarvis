"""Quick live test - all Jarvis systems."""
import pytest
pytestmark = pytest.mark.live
import asyncio
import sys
import time
import glob
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings
get_settings.cache_clear()

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"; D = "\033[90m"; RST = "\033[0m"
passed = failed = skipped = 0

def ok(msg, detail=""):
    global passed; passed += 1
    d = f" {D}({detail}){RST}" if detail else ""
    print(f"  {G}PASS{RST} {msg}{d}")

def fail(msg, detail=""):
    global failed; failed += 1
    d = f" {D}({detail}){RST}" if detail else ""
    print(f"  {R}FAIL{RST} {msg}{d}")

def skip(msg, detail=""):
    global skipped; skipped += 1
    d = f" {D}({detail}){RST}" if detail else ""
    print(f"  {Y}SKIP{RST} {msg}{d}")


async def run_all():
    global passed, failed, skipped
    t0 = time.time()

    print(f"\n{C}{'='*50}")
    print(f"   JARVIS LIVE TEST SUITE")
    print(f"{'='*50}{RST}\n")

    # 1. Ollama Connection
    print(f"{C}--- 1. Ollama Connection ---{RST}")
    import ollama
    try:
        client = ollama.AsyncClient(host="http://localhost:11434")
        models = await client.list()
        names = [m.model for m in models.models][:4]
        ok("Ollama running", ", ".join(names))
    except Exception as e:
        fail("Ollama connection", str(e))
        print(f"\n{R}Cannot continue without Ollama!{RST}")
        return

    # 2. Ollama Chat
    print(f"\n{C}--- 2. Ollama Chat ---{RST}")
    t = time.time()
    r = await client.chat(model="qwen3:8b", messages=[{"role": "user", "content": "say hello /no_think"}])
    content = r.message.content.strip()[:40]
    ok("LLM response", f"{content} ({time.time()-t:.1f}s)")

    # 3. Tool Calling
    print(f"\n{C}--- 3. Tool Calling ---{RST}")
    tool_def = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }]
    r = await client.chat(
        model="qwen3:8b",
        messages=[{"role": "user", "content": "Weather in Tel Aviv? /no_think"}],
        tools=tool_def,
    )
    if r.message.tool_calls:
        tc = r.message.tool_calls[0]
        ok("Tool calling", f"{tc.function.name}({tc.function.arguments})")
    else:
        fail("Tool calling", "No tool calls returned")

    # 4. Memory
    print(f"\n{C}--- 4. Memory System ---{RST}")
    from core.memory import Memory
    m = Memory(db_path=Path("data/test_mem.db"))
    await m.init()
    ok("DB initialized")
    await m.add_message("user", "test")
    await m.add_message("assistant", "reply")
    msgs = await m.get_recent_messages()
    ok("Messages stored", f"{len(msgs)} messages") if len(msgs) == 2 else fail("Messages")
    await m.set_fact("name", "Jarvis")
    f = await m.get_fact("name")
    ok("Facts work", f"name={f}") if f == "Jarvis" else fail("Facts")
    await m.close()
    Path("data/test_mem.db").unlink(missing_ok=True)

    # 5. Skill Registry
    print(f"\n{C}--- 5. Skill Registry ---{RST}")
    from core.skill_base import SkillRegistry
    from skills.creality_print import CrealityPrintSkill
    from skills.model_downloader import ModelDownloaderSkill
    from skills.appointment_booker import AppointmentBookerSkill
    from skills.spotify_controller import SpotifySkill
    from skills.code_writer import CodeWriterSkill

    reg = SkillRegistry()
    skill_list = [
        CrealityPrintSkill(),
        ModelDownloaderSkill(),
        AppointmentBookerSkill(),
        SpotifySkill(),
        CodeWriterSkill(),
    ]
    for s in skill_list:
        reg.register(s)
    ok(f"{len(reg.all_skills())} skills registered")
    all_tools = reg.get_all_tools()
    ok(f"{len(all_tools)} tool definitions generated")
    resolved = reg.resolve_tool_call("spotify_play")
    if resolved:
        ok("Tool resolve", f"spotify_play -> {resolved[0].name}.{resolved[1]}")
    else:
        fail("Tool resolve")

    # 6. Creality Print
    print(f"\n{C}--- 6. Creality Print ---{RST}")
    cp = CrealityPrintSkill()
    r = await cp.do_configure(layer_height="0.16", infill="30", supports="true")
    ok("Configure", str(r["settings"]))
    exe = Path(cp.settings.creality_print_exe)
    if exe.exists():
        ok("EXE found", str(exe))
    else:
        fail("EXE not found", str(exe))
    stls = glob.glob("C:/Users/User/Desktop/3D Models/*.stl")
    ok(f"{len(stls)} STL files available")

    # 7. Model Downloader
    print(f"\n{C}--- 7. Model Downloader ---{RST}")
    md = ModelDownloaderSkill()
    r = await md.do_search("benchy")
    if r["count"] > 0:
        first_title = r["results"][0]["title"][:40]
        ok("Search", f"{r['count']} results, first: {first_title}")
    else:
        skip("Search returned 0 results (sites may block scraping)")
    r = await md.do_list_downloads()
    ok("List downloads", f"{r['count']} files")

    # 8. Spotify
    print(f"\n{C}--- 8. Spotify ---{RST}")
    settings = get_settings()
    if not settings.spotipy_client_id:
        skip("Spotify not configured (set JARVIS_SPOTIPY_CLIENT_ID in .env)")
    else:
        try:
            sp = SpotifySkill()
            r = await sp.do_current()
            ok("Spotify connected", r.get("track", "idle"))
        except Exception as e:
            fail("Spotify", str(e))

    # 9. Code Writer
    print(f"\n{C}--- 9. Code Writer ---{RST}")
    cw = CodeWriterSkill()
    t = time.time()
    r = await cw.do_write(
        prompt="Write a Python one-liner that prints the sum of numbers 1 to 10. /no_think",
        filename="sum_test.py",
    )
    if r.get("error"):
        fail("Code generation", r["error"])
    else:
        ok("Code generated", f"{r['lines']} lines in {time.time()-t:.1f}s")
        rr = await cw.do_run(file_path=r["file"])
        if rr.get("status") == "ok":
            ok("Code executed", f"stdout: {rr['stdout'].strip()[:60]}")
        else:
            err = rr.get("stderr", rr.get("error", ""))[:80]
            fail("Code execution", err)
        lr = await cw.do_list()
        ok(f"{lr['count']} files in generated_code")

    # 10. Appointment Booker
    print(f"\n{C}--- 10. Appointment Booker (Calmark) ---{RST}")
    ab = AppointmentBookerSkill()
    if ab.barber_name:
        ok("Barber configured", ab.barber_name)
    else:
        fail("No barber name")
    if "calmark" in ab.barber_url:
        ok("Calmark URL", ab.barber_url)
    else:
        fail("Bad URL", ab.barber_url)
    r = await ab.do_book_appointment()
    if r["status"] == "need_info":
        ok("Booking requires date+time")
    else:
        fail("No validation")
    skip("Browser test (skipping to avoid hang)")

    # 11. TTS
    print(f"\n{C}--- 11. Text-to-Speech ---{RST}")
    try:
        from voice.tts import TextToSpeech
        tts = TextToSpeech()
        tts.init()
        ok("TTS engine initialized")
        await tts.speak("Jarvis systems online")
        ok("TTS spoke")
    except Exception as e:
        fail("TTS", str(e))

    # 12. Orchestrator
    print(f"\n{C}--- 12. Orchestrator (Full Pipeline) ---{RST}")
    from core.orchestrator import Orchestrator
    mem = Memory(db_path=Path("data/test_orch.db"))
    await mem.init()
    orch = Orchestrator(reg, mem)
    t = time.time()
    try:
        response = await orch.process("Hello! What can you do? Answer in one sentence. /no_think")
        if response:
            ok("Orchestrator responded", f"({time.time()-t:.1f}s) {response[:80]}")
        else:
            fail("Empty response")
    except Exception as e:
        fail("Orchestrator", str(e))
    await mem.close()
    Path("data/test_orch.db").unlink(missing_ok=True)

    # Summary
    total = passed + failed + skipped
    elapsed = time.time() - t0
    print(f"\n{C}{'='*50}{RST}")
    print(f"  {G}PASSED: {passed}{RST}  {R}FAILED: {failed}{RST}  {Y}SKIPPED: {skipped}{RST}  (total: {total}, {elapsed:.1f}s)")
    print(f"{C}{'='*50}{RST}\n")

    if failed:
        print(f"{R}Some tests failed - see above for details{RST}")
    else:
        print(f"{G}All tests passed!{RST}")


if __name__ == "__main__":
    asyncio.run(run_all())
