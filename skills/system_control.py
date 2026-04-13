"""System control skill — run shell commands, open apps, manage files, control the PC."""

import asyncio
import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

# Max output length to avoid flooding the LLM context
MAX_OUTPUT = 4000
# Command timeout in seconds
CMD_TIMEOUT = 30


class SystemControlSkill(BaseSkill):
    name = "system"
    description = "Run shell commands, open applications, manage files, and control the computer"

    RISK_MAP = {
        "run_command": "high",
        "open_app": "medium",
        "open_file": "low",
        "list_files": "low",
        "file_info": "low",
        "read_file": "low",
        "write_file": "high",
        "delete_file": "high",
        "search_files": "low",
        "system_info": "low",
        "kill_process": "high",
        "list_processes": "low",
        "screenshot": "medium",
        "set_clipboard": "medium",
        "get_clipboard": "low",
        "open_url": "low",
        "shutdown_pc": "high",
        "volume": "low",
    }

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("system.%s failed", action)
            return {"error": str(e)}

    async def do_run_command(self, command: str, timeout: int = CMD_TIMEOUT) -> dict:
        """Run a shell command on the computer and return the output. Use for anything: git, npm, pip, dir, etc."""
        logger.info("Running command: %s", command)
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path.home() / "Desktop"),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()

            # Truncate if needed
            if len(out) > MAX_OUTPUT:
                out = out[:MAX_OUTPUT] + f"\n... (truncated, {len(stdout)} bytes total)"
            if len(err) > MAX_OUTPUT:
                err = err[:MAX_OUTPUT] + f"\n... (truncated)"

            return {
                "status": "ok" if proc.returncode == 0 else "error",
                "exit_code": proc.returncode,
                "stdout": out,
                "stderr": err,
            }
        except asyncio.TimeoutError:
            return {"error": f"Command timed out after {timeout}s"}

    async def do_open_app(self, app_name: str) -> dict:
        """Open an application by name (e.g., 'chrome', 'notepad', 'spotify', 'discord', 'whatsapp')."""
        logger.info("Opening app: %s", app_name)

        # Common app mappings for Windows
        app_map = {
            "chrome": "start chrome",
            "google chrome": "start chrome",
            "firefox": "start firefox",
            "edge": "start msedge",
            "notepad": "start notepad",
            "calculator": "start calc",
            "מחשבון": "start calc",
            "paint": "start mspaint",
            "explorer": "start explorer",
            "סייר": "start explorer",
            "cmd": "start cmd",
            "terminal": "start wt",
            "powershell": "start powershell",
            "spotify": 'start "" "spotify:"',
            "discord": 'start "" "discord:"',
            "whatsapp": 'start "" "whatsapp:"',
            "telegram": 'start "" "telegram:"',
            "vscode": "code",
            "cursor": "cursor",
            "task manager": "start taskmgr",
            "settings": "start ms-settings:",
            "הגדרות": "start ms-settings:",
        }

        key = app_name.lower().strip()
        cmd = app_map.get(key)

        if not cmd:
            # Try to find the executable
            found = shutil.which(app_name)
            if found:
                cmd = f'start "" "{found}"'
            else:
                # Last resort: try start with the name directly
                cmd = f"start {app_name}"

        return await self.do_run_command(cmd, timeout=10)

    async def do_open_file(self, path: str) -> dict:
        """Open a file with the default application (e.g., open a PDF, image, document)."""
        p = Path(path)
        if not p.exists():
            return {"error": f"File not found: {path}"}
        os.startfile(str(p))
        return {"status": "ok", "message": f"Opened {p.name}"}

    async def do_open_url(self, url: str) -> dict:
        """Open a URL in the default browser."""
        import webbrowser
        webbrowser.open(url)
        return {"status": "ok", "message": f"Opened {url}"}

    async def do_list_files(self, directory: str = "", pattern: str = "*") -> dict:
        """List files in a directory. Defaults to Desktop. Use pattern like '*.pdf' to filter."""
        d = Path(directory) if directory else Path.home() / "Desktop"
        if not d.exists():
            return {"error": f"Directory not found: {d}"}
        files = []
        try:
            for f in sorted(d.glob(pattern))[:50]:
                try:
                    size = f.stat().st_size if f.is_file() else 0
                except OSError:
                    size = 0
                files.append({
                    "name": f.name,
                    "type": "dir" if f.is_dir() else "file",
                    "size_kb": round(size / 1024, 1),
                })
        except (PermissionError, OSError) as e:
            return {"error": f"Cannot list {d}: {e}"}
        return {"directory": str(d), "count": len(files), "files": files}

    async def do_search_files(self, query: str, directory: str = "") -> dict:
        """Search for files by name pattern. Example: query='*.stl' or query='report'."""
        d = Path(directory) if directory else Path.home() / "Desktop"
        if not d.exists():
            return {"error": f"Directory not found: {d}"}

        SKIP_DIRS = {
            "node_modules", ".gradle", ".git", "__pycache__", ".venv",
            "venv", ".cache", ".npm", ".nuget", "AppData", ".android",
            "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
        }

        results = []
        search = f"*{query}*" if "*" not in query else query

        def _search():
            found = []
            try:
                for f in d.rglob(search):
                    try:
                        if any(skip in f.parts for skip in SKIP_DIRS):
                            continue
                        found.append(str(f))
                        if len(found) >= 30:
                            break
                    except (PermissionError, FileNotFoundError, OSError):
                        continue
            except (PermissionError, FileNotFoundError, OSError):
                pass
            return found

        results = await asyncio.get_event_loop().run_in_executor(None, _search)
        return {"query": query, "count": len(results), "results": results}

    async def do_file_info(self, path: str) -> dict:
        """Get detailed info about a file (size, dates, type)."""
        p = Path(path)
        if not p.exists():
            return {"error": f"File not found: {path}"}
        stat = p.stat()
        from datetime import datetime
        return {
            "name": p.name,
            "path": str(p),
            "size_kb": round(stat.st_size / 1024, 1),
            "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "is_dir": p.is_dir(),
            "extension": p.suffix,
        }

    async def do_read_file(self, path: str, max_lines: int = 100) -> dict:
        """Read the contents of a text file. Returns first max_lines lines."""
        p = Path(path)
        if not p.exists():
            return {"error": f"File not found: {path}"}
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            truncated = len(lines) > max_lines
            content = "\n".join(lines[:max_lines])
            if len(content) > MAX_OUTPUT:
                content = content[:MAX_OUTPUT] + "\n... (truncated)"
            return {
                "path": str(p),
                "lines": min(len(lines), max_lines),
                "total_lines": len(lines),
                "truncated": truncated,
                "content": content,
            }
        except Exception as e:
            return {"error": f"Cannot read file: {e}"}

    async def do_write_file(self, path: str, content: str) -> dict:
        """Write content to a file. Creates the file if it doesn't exist."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"status": "ok", "path": str(p), "size_kb": round(p.stat().st_size / 1024, 1)}

    async def do_delete_file(self, path: str) -> dict:
        """Delete a file (moves to recycle bin on Windows if possible)."""
        p = Path(path)
        if not p.exists():
            return {"error": f"File not found: {path}"}
        try:
            # Try send2trash for safe deletion
            from send2trash import send2trash
            send2trash(str(p))
            return {"status": "ok", "message": f"Moved {p.name} to recycle bin"}
        except ImportError:
            # Fallback: actual delete
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            return {"status": "ok", "message": f"Deleted {p.name} (permanent)"}

    async def do_list_processes(self, filter_name: str = "") -> dict:
        """List running processes, optionally filtered by name."""
        result = await self.do_run_command("tasklist /FO CSV /NH", timeout=10)
        if result.get("status") != "ok":
            return result

        processes = []
        for line in result["stdout"].splitlines():
            parts = line.strip().strip('"').split('","')
            if len(parts) >= 5:
                name = parts[0]
                if filter_name and filter_name.lower() not in name.lower():
                    continue
                processes.append({
                    "name": name,
                    "pid": parts[1],
                    "mem_kb": parts[4].replace('"', '').strip(),
                })
                if len(processes) >= 30:
                    break

        return {"count": len(processes), "processes": processes}

    async def do_kill_process(self, name_or_pid: str) -> dict:
        """Kill a process by name or PID. Example: 'chrome.exe' or '12345'."""
        if name_or_pid.isdigit():
            cmd = f"taskkill /PID {name_or_pid} /F"
        else:
            cmd = f"taskkill /IM {name_or_pid} /F"
        return await self.do_run_command(cmd, timeout=10)

    async def do_system_info(self) -> dict:
        """Get basic system information (OS, CPU, RAM, disk)."""
        import psutil
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")
        return {
            "os": f"{platform.system()} {platform.release()}",
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "cpu_percent": psutil.cpu_percent(interval=0.5),
            "ram_total_gb": round(mem.total / (1024**3), 1),
            "ram_used_gb": round(mem.used / (1024**3), 1),
            "ram_percent": mem.percent,
            "disk_total_gb": round(disk.total / (1024**3), 1),
            "disk_free_gb": round(disk.free / (1024**3), 1),
            "disk_percent": disk.percent,
        }

    async def do_screenshot(self, save_path: str = "") -> dict:
        """Take a screenshot of the entire screen. With a vision model (e.g. qwen3-vl), Jarvis receives the image for the next reply — describe or answer in Hebrew as the user asked."""
        try:
            import pyautogui
            if not save_path:
                save_path = str(Path.home() / "Desktop" / "screenshot.png")
            img = pyautogui.screenshot()
            img.save(save_path)
            return {"status": "ok", "path": save_path}
        except ImportError:
            return {"error": "pyautogui not installed. Run: pip install pyautogui"}

    async def do_set_clipboard(self, text: str) -> dict:
        """Copy text to clipboard."""
        proc = await asyncio.create_subprocess_shell(
            "clip",
            stdin=asyncio.subprocess.PIPE,
        )
        await proc.communicate(text.encode("utf-16-le"))
        return {"status": "ok", "message": "Text copied to clipboard"}

    async def do_get_clipboard(self) -> dict:
        """Get current clipboard text content."""
        result = await self.do_run_command(
            'powershell -command "Get-Clipboard"', timeout=5
        )
        return {"text": result.get("stdout", "")}

    async def do_volume(self, level: int = -1) -> dict:
        """Set system volume (0-100), or get current volume if level=-1."""
        if level == -1:
            # Get current volume
            cmd = (
                'powershell -command "'
                'Add-Type -TypeDefinition @\\"\\nusing System.Runtime.InteropServices;\\n'
                '[Guid(\\"5CDF2C82-841E-4546-9722-0CF74078229A\\"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\\n'
                'interface IAudioEndpointVolume { int _0(); int _1(); int _2(); int _3();\\n'
                'int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext);\\n'
                'int GetMasterVolumeLevelScalar(out float pfLevel); }\\n'
                '@\\"\\n'
                '"'
            )
            # Simpler approach
            return await self.do_run_command(
                'powershell -command "(Get-AudioDevice -PlaybackVolume)"',
                timeout=5,
            )
        else:
            level = max(0, min(100, level))
            # Use nircmd if available, otherwise powershell
            nircmd = shutil.which("nircmd")
            if nircmd:
                vol = int(level * 655.35)
                return await self.do_run_command(f'nircmd setsysvolume {vol}', timeout=5)
            else:
                return await self.do_run_command(
                    f'powershell -command "(New-Object -ComObject WScript.Shell).SendKeys([char]173)"',
                    timeout=5,
                )

    async def do_shutdown_pc(self, action: str = "shutdown", delay: int = 60) -> dict:
        """Shutdown, restart, or sleep the computer. action: 'shutdown', 'restart', 'sleep', 'cancel'."""
        cmds = {
            "shutdown": f"shutdown /s /t {delay}",
            "restart": f"shutdown /r /t {delay}",
            "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
            "cancel": "shutdown /a",
            "lock": "rundll32.exe user32.dll,LockWorkStation",
        }
        cmd = cmds.get(action)
        if not cmd:
            return {"error": f"Unknown action: {action}. Use: shutdown, restart, sleep, cancel, lock"}
        return await self.do_run_command(cmd, timeout=10)
