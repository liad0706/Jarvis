"""
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝

Your personal AI assistant - 100% free & local.
"""

import asyncio
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.async_input import async_input as cli_async_input
from core.bootstrap import bootstrap, shutdown
from core.dev_reload import AutoReloadWatcher, build_restart_reason, build_resume_message, summarize_changed_files
from core.skill_base import SkillRegistry
from core.state_machine import TaskStateMachine

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("cp"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("jarvis").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
logging.getLogger("pyatv").setLevel(logging.CRITICAL)
logger = logging.getLogger("jarvis")

PROJECT_ROOT = Path(__file__).resolve().parent

BANNER = """
\033[96m
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝
\033[0m
\033[93m  Your Personal AI Assistant - 100% Free & Local\033[0m
\033[90m  ─────────────────────────────────────────────────
  Skills: Creality Print | 3D Models | Spotify
          Appointments | Code Writer | System Control
          Self-Improve | Scheduler
  ─────────────────────────────────────────────────\033[0m
  Type \033[92m'help'\033[0m for commands, \033[91m'quit'\033[0m to exit.
"""


PENDING_CHANGES_PATH = PROJECT_ROOT / "data" / "pending_changes.json"


def _show_pending_changes():
    """Show and clear any pending changelog entries from the developer."""
    import json as _json
    if not PENDING_CHANGES_PATH.exists():
        return
    try:
        entries = _json.loads(PENDING_CHANGES_PATH.read_text(encoding="utf-8"))
        if not entries:
            return
        print("\033[93m  ╔══════════════════════════════════════╗")
        print("  ║     🔧  עדכונים חדשים בקוד!         ║")
        print("  ╚══════════════════════════════════════╝\033[0m")
        for entry in entries:
            date = entry.get("date", "")
            changes = entry.get("changes", [])
            if date:
                print(f"\033[90m  ({date})\033[0m")
            for change in changes:
                print(f"\033[96m    • {change}\033[0m")
        print()
        # Clear after showing
        PENDING_CHANGES_PATH.write_text("[]", encoding="utf-8")
    except Exception:
        pass


def _get_lan_ip() -> str | None:
    """Get local LAN IP address for mobile access."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def _tcp_listen_port_free(host: str, port: int) -> bool:
    """True if we can bind now (port likely free for uvicorn)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


class WhatsAppPortBusy(RuntimeError):
    """Raised when 127.0.0.1:whatsapp_api_port cannot be bound."""


def _is_port_already_in_use(err: OSError) -> bool:
    if getattr(err, "winerror", None) == 10048:
        return True
    if err.errno in (98, 48, 10048):  # EADDRINUSE (Linux/mac/Windows variants)
        return True
    msg = str(err).lower()
    return "address already in use" in msg or "only one usage" in msg


async def _run_whatsapp_api(ctx):
    """Run uvicorn; log clearly if the port is taken (no scary traceback)."""
    from whatsapp_server import run_server

    try:
        await run_server(ctx)
    except OSError as e:
        if _is_port_already_in_use(e):
            p = ctx.settings.whatsapp_api_port
            logger.error(
                "WhatsApp API cannot bind 127.0.0.1:%s — port already in use. "
                "Close the other Jarvis / whatsapp_server, or set JARVIS_WHATSAPP_API_PORT.",
                p,
            )
            print(
                f"\033[91m  WhatsApp API failed: port {p} is busy.\033[0m\n"
                f"\033[90m  Fix: close the other process, or change JARVIS_WHATSAPP_API_PORT in .env\033[0m\n"
                f"\033[90m  PowerShell: Get-NetTCPConnection -LocalPort {p} | Select OwningProcess\033[0m\n"
            )
            raise WhatsAppPortBusy(f"Port {p} in use") from e
        raise


async def _start_whatsapp(ctx):
    """Launch the WhatsApp API server + Node.js Baileys bridge as background tasks."""
    bridge_proc = None
    api_task = None
    port = ctx.settings.whatsapp_api_port
    host = "127.0.0.1"

    if not _tcp_listen_port_free(host, port):
        logger.error("WhatsApp skipped: port %s already in use", port)
        print(
            f"\033[91m  WhatsApp skipped: port {port} is already in use.\033[0m\n"
            f"\033[90m  Another Jarvis or whatsapp_server.py is probably still running.\033[0m\n"
            f"\033[90m  PowerShell: Get-NetTCPConnection -LocalPort {port} | Select OwningProcess\033[0m\n"
            f"\033[90m  Then: Stop-Process -Id <PID> -Force\033[0m\n"
        )
        return None, None, False

    try:
        api_task = asyncio.create_task(_run_whatsapp_api(ctx))
        logger.info("WhatsApp API server starting on port %s", port)

        await asyncio.sleep(2)

        if api_task.done():
            exc = api_task.exception()
            if exc is not None:
                if not isinstance(exc, WhatsAppPortBusy):
                    logger.exception("WhatsApp API exited before bridge start", exc_info=exc)
                return api_task, None, False

        bridge_script = PROJECT_ROOT / "whatsapp" / "bridge.mjs"
        if not bridge_script.exists():
            logger.error("WhatsApp bridge not found at %s", bridge_script)
            return api_task, None, False

        bridge_proc = subprocess.Popen(
            ["node", str(bridge_script)],
            cwd=str(PROJECT_ROOT / "whatsapp"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("WhatsApp bridge started (PID %d)", bridge_proc.pid)

    except Exception as e:
        logger.exception("Failed to start WhatsApp: %s", e)
        return api_task, bridge_proc, False

    return api_task, bridge_proc, bridge_proc is not None


async def _stop_whatsapp(api_task, bridge_proc):
    """Cleanly shut down WhatsApp components."""
    if bridge_proc and bridge_proc.poll() is None:
        logger.info("Stopping WhatsApp bridge (PID %d)...", bridge_proc.pid)
        bridge_proc.terminate()
        try:
            bridge_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bridge_proc.kill()

    if api_task:
        if api_task.done():
            try:
                api_task.result()
            except (WhatsAppPortBusy, asyncio.CancelledError):
                pass
            except Exception:
                pass
            api_task.cancel()
            try:
                await api_task
            except asyncio.CancelledError:
                pass


_restart_requested = False
_restart_event: asyncio.Event | None = None


def _queue_restart(**_kwargs):
    global _restart_requested
    _restart_requested = True
    if _restart_event is not None:
        _restart_event.set()


def _build_reexec_argv() -> list[str]:
    orig_argv = getattr(sys, "orig_argv", None)
    if orig_argv:
        return list(orig_argv)
    if getattr(sys, "frozen", False):
        return [sys.executable, *sys.argv[1:]]
    return [sys.executable, *sys.argv]


def _restart_process():
    argv = _build_reexec_argv()
    executable = argv[0]
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execv(executable, argv)
    except OSError:
        logger.exception("Fresh process restart failed; falling back to spawning a replacement process")
        subprocess.Popen(argv, cwd=str(PROJECT_ROOT))
        raise SystemExit(0)


async def run_jarvis():
    global _restart_requested, _restart_event
    _restart_requested = False
    _restart_event = asyncio.Event()

    print(BANNER)

    ctx = await bootstrap()
    api_task = None
    bridge_proc = None
    channel_manager = None

    from core.ollama_diag import warn_if_ollama_models_missing

    await warn_if_ollama_models_missing(ctx.settings)

    # --- Wire up restart skill ---
    restart_skill = ctx.registry.get("restart")
    if restart_skill:
        restart_skill.set_shutdown_callback(_queue_restart)

    # --- Check if we're resuming from a self-restart ---
    from skills.restart import has_pending_restart, clear_restart_context

    restart_ctx = has_pending_restart()
    if restart_ctx:
        msg = restart_ctx.get("resume_message", "הופעל מחדש בהצלחה.")
        reason = restart_ctx.get("reason", "")
        source = restart_ctx.get("source", "")
        changed_files = restart_ctx.get("changed_files", []) or []
        print(f"\033[92m  [Restart] {msg}\033[0m")
        if reason:
            print(f"\033[90m  סיבה: {reason}\033[0m")
        if source:
            print(f"\033[90m  Source: {source}\033[0m")
        if changed_files:
            summary = summarize_changed_files(changed_files, PROJECT_ROOT, limit=5)
            print(f"\033[90m  Files:  {summary}\033[0m")
        print()
        clear_restart_context()

    # --- WhatsApp ---
    if ctx.settings.whatsapp_enabled:
        print("\033[95m  WhatsApp integration enabled — starting...\033[0m")
        api_task, bridge_proc, whatsapp_ready = await _start_whatsapp(ctx)
        if whatsapp_ready:
            print("\033[95m  WhatsApp is live! Messages will be handled in background.\033[0m\n")
            print("\033[93m  Jarvis CLI works; WhatsApp is not fully up (see messages above).\033[0m\n")
    else:
        logger.info("WhatsApp disabled (set JARVIS_WHATSAPP_ENABLED=true to enable)")

    # --- Channels (Telegram, Discord, etc.) ---
    if ctx.settings.telegram_token or getattr(ctx.settings, "discord_token", ""):
        from channels.manager import create_channel_manager
        channel_manager = create_channel_manager(ctx.settings)

        async def _channel_handler(msg) -> str:
            return await ctx.orchestrator.process(msg.text)

        channel_task = asyncio.create_task(channel_manager.start_all(_channel_handler))
        active = channel_manager.active_channels
        if active:
            print(f"\033[95m  Channels active: {', '.join(active)}\033[0m\n")

    # --- Crash recovery ---
    recovered = TaskStateMachine.from_checkpoint(planner=ctx.planner, event_bus=ctx.event_bus)
    if recovered:
        print(f"\033[93m[Recovery] Found incomplete task: {recovered.plan.goal[:60]}\033[0m")
        try:
            answer = await cli_async_input("  Resume? (y/n): ")
            if answer.strip().lower() in ("y", "yes", "כן"):
                print("\033[90m[Resuming...]\033[0m")
                result = await recovered.run(ctx.orchestrator)
                print(f"\n\033[93mJarvis:\033[0m {result}\n")
            else:
                recovered.clear_checkpoint()
        except (EOFError, KeyboardInterrupt):
            recovered.clear_checkpoint()

    # --- Optional voice ---
    stt = None
    tts = None
    if ctx.settings.voice_enabled:
        try:
            from voice.tts import TextToSpeech

            tts = TextToSpeech()
            tts.init()
            logger.info("TTS (ElevenLabs) ready")
        except Exception as e:
            logger.warning("TTS initialization failed: %s", e)
            tts = None

        try:
            from voice.stt import SpeechToText

            stt = SpeechToText()
            stt.init()
            logger.info("STT (Google Speech) ready")
        except Exception as e:
            logger.warning("STT initialization failed: %s", e)
            stt = None

    if tts:
        ctx.proactive_engine.tts = tts

    # --- Voice loop (hands-free mode) ---
    voice_loop_task = None
    if ctx.settings.voice_enabled and stt and tts:
        try:
            from voice.voice_loop import VoiceLoop
            _voice_loop = VoiceLoop(stt=stt, tts=tts, orchestrator=ctx.orchestrator)
            voice_loop_task = asyncio.create_task(_voice_loop.run_forever())
            logger.info("VoiceLoop started")
        except Exception as e:
            logger.warning("VoiceLoop init failed: %s", e)

    # --- Dashboard ---
    dashboard_task = None
    if ctx.settings.dashboard_enabled:
        from dashboard.server import (
            bridge_components,
            bridge_event_bus,
            bridge_orchestrator,
            bridge_tts,
            run_dashboard,
            broadcast as dash_broadcast,
            is_chat_busy as dashboard_is_chat_busy,
        )
        bridge_event_bus(ctx.event_bus)
        bridge_orchestrator(ctx.orchestrator)
        if tts:
            bridge_tts(tts)
        bridge_components(
            orchestrator=ctx.orchestrator,
            memory_manager=ctx.memory_manager,
            awareness=ctx.awareness,
            metrics=ctx.metrics,
            notifications=ctx.notifications,
            automation_engine=ctx.automation_engine,
            registry=ctx.registry,
            skill_store=ctx.skill_store,
            model_router=ctx.model_router,
        )
        ctx.proactive_engine.broadcast = dash_broadcast
        dash_port = ctx.settings.dashboard_port
        dash_host = ctx.settings.dashboard_host
        if _tcp_listen_port_free("0.0.0.0", dash_port):
            dashboard_task = asyncio.create_task(run_dashboard(host=dash_host, port=dash_port))
            dash_url = f"http://127.0.0.1:{dash_port}"
            print(f"\033[96m  Dashboard: {dash_url}\033[0m")
            # Show LAN URL for mobile access
            lan_ip = _get_lan_ip()
            if lan_ip:
                mobile_url = f"http://{lan_ip}:{dash_port}/mobile"
                print(f"\033[96m  Mobile:    {mobile_url}\033[0m")
            print()
            import webbrowser
            webbrowser.open(dash_url)
            print(f"\033[93m  Dashboard skipped: port {dash_port} is busy.\033[0m\n")
            dashboard_is_chat_busy = lambda: False
    else:
        dashboard_is_chat_busy = lambda: False

    # --- Scheduler ---
    ctx.scheduler.start()
    sched_count = len(ctx.scheduler.list_schedules())
    if sched_count:
        print(f"\033[95m  Scheduler: {sched_count} scheduled task(s) active\033[0m")
        for s in ctx.scheduler.list_schedules():
            days_str = "כל יום" if s["days"] is None else str(s["days"])
            status = "✅" if s["enabled"] else "⏸️"
            print(f"\033[90m    {status} {s['name']} → {s['hour']:02d}:{s['minute']:02d} ({days_str})\033[0m")
        print()

    auto_reload_task = None
    if ctx.settings.dev_auto_reload:
        watcher = AutoReloadWatcher(PROJECT_ROOT)
        watcher.prime()
        print("\033[95m  Auto-reload: watching source files for changes\033[0m\n")

        async def auto_reload_loop():
            from core.notifications import NotificationLevel
            from skills.restart import save_restart_context

            pending_changes: list[Path] = []
            last_change_at = 0.0
            poll_delay = max(0.2, float(ctx.settings.dev_auto_reload_poll_seconds))
            quiet_window = max(poll_delay, float(ctx.settings.dev_auto_reload_quiet_seconds))

            while not _restart_requested:
                changed = await asyncio.to_thread(watcher.scan_changes)
                if changed:
                    pending_changes = changed
                    last_change_at = asyncio.get_running_loop().time()
                elif pending_changes and (asyncio.get_running_loop().time() - last_change_at) >= quiet_window:
                    changed_files = [str(path) for path in pending_changes]
                    summary = summarize_changed_files(changed_files, PROJECT_ROOT)
                    save_restart_context(
                        reason=build_restart_reason(changed_files, PROJECT_ROOT),
                        resume_message=build_resume_message(changed_files, PROJECT_ROOT),
                        source="auto_reload",
                        changed_files=changed_files,
                    )
                    if ctx.notifications:
                        await ctx.notifications.notify(
                            "Jarvis restarting",
                            f"Code changes detected in {summary}",
                            level=NotificationLevel.INFO,
                            source="dev_reload",
                        )
                    _queue_restart(source="auto_reload", changed_files=changed_files)
                    return
                await asyncio.sleep(poll_delay)

        auto_reload_task = asyncio.create_task(auto_reload_loop())

    # --- Show pending changes from developer (Claude Code) ---
    _show_pending_changes()

    print("\n\033[92mJarvis is ready. How can I help?\033[0m\n")

    # --- Action journal background flush ---
    await ctx.action_journal.start()

    # --- Proactive engine + pattern analysis (background) ---
    async def proactive_loop():
        cycle = 0
        while True:
            await ctx.proactive_engine.check()
            cycle += 1
            # Run pattern analysis every 6 cycles (30 min)
            if cycle % 6 == 0:
                try:
                    ctx.pattern_learner.analyze()
                except Exception:
                    pass
            await asyncio.sleep(300)

    proactive_task = asyncio.create_task(proactive_loop())

    # --- Main loop ---
    stdin_tty = bool(sys.stdin and sys.stdin.isatty())

    _has_channels = channel_manager is not None and bool(channel_manager.active_channels)
    # On Windows, sys.stdin.isatty() returns True even for background/redirected processes,
    # so we can't rely on it alone. If channels (Telegram/Discord) are active, always go headless.
    _force_headless = _has_channels
    if (dashboard_task is not None or _force_headless) and (not stdin_tty or _force_headless):
        if dashboard_task is not None:
            print(
                "\033[90m  No interactive stdin — use the web dashboard for chat. "
                "(CLI loop skipped)\033[0m\n"
            )
        else:
            print(
                "\033[90m  No interactive stdin — running headless with active channels. "
                "(CLI loop skipped)\033[0m\n"
            )
        while True:
            if _restart_requested:
                if dashboard_task is not None and dashboard_is_chat_busy():
                    await asyncio.sleep(0.2)
                    continue
                print("\n\033[93m  Jarvis is restarting...\033[0m\n")
                break
            if dashboard_task is not None and dashboard_task.done() and not _has_channels:
                # Only exit when dashboard stops if there are no active channels keeping us alive
                try:
                    await dashboard_task
                except asyncio.CancelledError:
                    pass
                break
            await asyncio.sleep(0.25)
    else:
        while True:
            # Check if restart was requested by the restart skill
            if _restart_requested:
                print("\n\033[93m  Jarvis is restarting...\033[0m\n")
                break

            try:
                if stt and tts:
                    print("\033[90m[Type message, or 'v' + Enter to speak]\033[0m")
                    input_task = asyncio.create_task(_get_input_with_voice(stt))
                else:
                    input_task = asyncio.create_task(cli_async_input("\033[96mYou:\033[0m "))

                restart_wait_task = asyncio.create_task(_restart_event.wait())
                done, pending = await asyncio.wait(
                    {input_task, restart_wait_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if restart_wait_task in done and _restart_requested:
                    print("\n\033[93m  Jarvis is restarting...\033[0m\n")
                    break
                user_input = await input_task

                user_input = user_input.strip()

                if not user_input:
                    continue

                if user_input.lower() in ("quit", "exit", "bye", "יציאה"):
                    print("\n\033[93mJarvis:\033[0m Goodbye! 👋\n")
                    if tts:
                        await tts.speak("Goodbye!")
                    break

                if user_input.lower() in ("help", "עזרה"):
                    _print_help(ctx.registry)
                    continue

                if user_input.lower() in ("schedules", "לוז", "משימות מתוזמנות"):
                    _print_schedules(ctx.scheduler)
                    continue

                if user_input.lower().startswith("run "):
                    routine_name = user_input[4:].strip()
                    print(f"\033[90m[Running routine: {routine_name}...]\033[0m")
                    result = await ctx.scheduler.run_now(routine_name)
                    if "error" in result:
                        print(f"\033[91m  Error: {result['error']}\033[0m\n")
                    else:
                        print(f"\n\033[93mJarvis:\033[0m\n{result.get('summary', str(result))}\n")
                    continue

                _EVENING_TRIGGERS = {"לילה טוב", "evening routine", "רוטינת ערב"}
                if user_input.strip() in _EVENING_TRIGGERS:
                    print("\033[90m[Running evening routine...]\033[0m")
                    result = await ctx.smart_runner.run("evening_routine")
                    summary = result.get("summary") or result.get("detail") or str(result)
                    print(f"\n\033[93mJarvis:\033[0m\n{summary}\n")
                    if tts:
                        await tts.speak(summary)
                    continue

                if user_input.lower() in ("login codex", "codex login", "התחבר לcodex"):
                    from core.codex_auth import login_interactive
                    token = await login_interactive()
                    if token:
                        # Switch provider to Codex OAuth
                        ctx.settings.llm_provider = "codex"
                        ctx.orchestrator._provider = None  # force re-init
                        print("\033[92m  Jarvis now uses Codex (ChatGPT subscription)!\033[0m\n")
                    else:
                        print("\033[91m  Login failed. Try again.\033[0m\n")
                    continue

                print("\033[90m[Processing...]\033[0m")
                response = await ctx.orchestrator.handle(user_input)

                print(f"\n\033[93mJarvis:\033[0m {response}\n")
                for img in getattr(ctx.orchestrator, "outgoing_chat_images", []) or []:
                    print(f"\033[90m  [תמונה לצ'אט] {img}\033[0m")

                if tts:
                    await tts.speak(response)

                # Check again after processing (restart might have been triggered by a tool call)
                if _restart_requested:
                    print("\n\033[93m  Jarvis is restarting...\033[0m\n")
                    break

            except KeyboardInterrupt:
                print("\n\n\033[93mJarvis:\033[0m See you later!\n")
                break
            except EOFError:
                print("\n\033[93mJarvis:\033[0m stdin closed — exiting.\033[0m\n")
                break
            except Exception as e:
                logger.exception("Error in main loop")
                print(f"\n\033[91mError: {e}\033[0m\n")

    # --- Shutdown ---
    if auto_reload_task and not auto_reload_task.done():
        auto_reload_task.cancel()
        try:
            await auto_reload_task
        except asyncio.CancelledError:
            pass
    if proactive_task and not proactive_task.done():
        proactive_task.cancel()
        try:
            await proactive_task
        except asyncio.CancelledError:
            pass
    if dashboard_task and not dashboard_task.done():
        dashboard_task.cancel()
        try:
            await dashboard_task
        except asyncio.CancelledError:
            pass
    await ctx.action_journal.stop()
    await _stop_whatsapp(api_task, bridge_proc)
    if channel_manager:
        await channel_manager.stop_all()
    await shutdown(ctx)
    _restart_event = None


async def _get_input_with_voice(stt):
    """Type normally, or type 'v' + Enter to speak instead."""
    try:
        text = await cli_async_input("\033[96mYou:\033[0m ")
        if text.strip().lower() == "v":
            print("\033[90m[🎤 Listening...]\033[0m")
            heard = await stt.listen()
            if heard and heard.strip():
                print(f"\033[96mYou (voice):\033[0m {heard}")
                return heard
            print("\033[90m[No speech detected, try again]\033[0m")
            return ""
        return text
    except EOFError:
        return "quit"


def _print_schedules(scheduler):
    schedules = scheduler.list_schedules()
    if not schedules:
        print("\033[93m  אין משימות מתוזמנות.\033[0m\n")
        return
    print("\n\033[93m── Scheduled Tasks ──\033[0m\n")
    for s in schedules:
        days_str = "כל יום" if s["days"] is None else str(s["days"])
        status = "\033[92m✅\033[0m" if s["enabled"] else "\033[90m⏸️\033[0m"
        last = s.get("last_run", "never") or "never"
        print(f"  {status} \033[96m{s['name']}\033[0m")
        print(f"    Routine: {s['routine']} | Time: {s['hour']:02d}:{s['minute']:02d} | Days: {days_str}")
        print(f"    Last run: {last}\n")
    print("  Tip: type \033[92mrun morning_routine\033[0m or \033[92mrun evening_routine\033[0m to trigger manually\n")


def _print_help(registry: SkillRegistry):
    print("\n\033[93m── Jarvis Help ──\033[0m\n")
    print("Available skills:\n")
    for skill in registry.all_skills():
        actions = ", ".join(skill.get_actions())
        print(f"  \033[96m{skill.name}\033[0m: {skill.description}")
        print(f"    Actions: {actions}\n")
    print("Commands:")
    print("  \033[92mhelp\033[0m          - Show this help")
    print("  \033[92mschedules\033[0m     - Show scheduled tasks")
    print("  \033[92mrun <routine>\033[0m - Run a routine manually (e.g., run morning_routine)")
    print("  \033[92mlogin codex\033[0m   - Connect to ChatGPT (Codex OAuth)")
    print("  \033[91mquit\033[0m          - Exit Jarvis")
    print()
    print("Just talk naturally! Examples:")
    print('  "Search for a Benchy model and download it"')
    print('  "Open Creality Print and import the last downloaded model"')
    print('  "Play Bohemian Rhapsody on Spotify"')
    print('  "Book me an appointment with Yishi Peretz for Thursday"')
    print('  "Write a Python script that sorts a list"')
    print()


if __name__ == "__main__":
    asyncio.run(run_jarvis())

    if _restart_requested:
            # Restart was requested by the restart skill — loop back and boot again.
            # Conversation history is on disk (data/conversation_state.json).
            # Restart context is in data/restart_context.json.
            print("\033[90m  Reloading with a fresh process...\033[0m\n")
            # Clear cached settings so fresh .env / code changes take effect
            from config import get_settings
            get_settings.cache_clear()
            _restart_process()
            # Normal exit (quit, Ctrl+C, EOF) — stop the loop.
