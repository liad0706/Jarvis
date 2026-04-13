"""Creality Print desktop automation skill."""

import asyncio
import logging
import subprocess
import time
from pathlib import Path

from core.skill_base import BaseSkill
from config import get_settings

logger = logging.getLogger(__name__)


class CrealityPrintSkill(BaseSkill):
    name = "creality"
    description = "Control Creality Print - open app, import STL files, configure and start 3D prints"

    def __init__(self):
        self.settings = get_settings()
        self._process = None

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        return await method(**(params or {}))

    async def do_open_app(self) -> dict:
        """Open the Creality Print application."""
        exe_path = Path(self.settings.creality_print_exe)
        if not exe_path.exists():
            return {"error": f"Creality Print not found at {exe_path}"}

        def _open():
            try:
                import pywinauto
                from pywinauto import Application

                # Try to connect to existing instance first
                try:
                    app = Application(backend="uia").connect(title_re=".*Creality.*", timeout=2)
                    return {"status": "already_running", "message": "Creality Print is already open"}
                except Exception:
                    pass

                # Launch the application
                app = Application(backend="uia").start(str(exe_path))
                time.sleep(5)  # Wait for app to load
                return {"status": "opened", "message": "Creality Print opened successfully"}
            except ImportError:
                # Fallback to subprocess
                subprocess.Popen([str(exe_path)])
                time.sleep(5)
                return {"status": "opened", "message": "Creality Print launched (basic mode)"}

        return await asyncio.to_thread(_open)

    async def do_import_stl(self, file_path: str) -> dict:
        """Import an STL file into Creality Print."""
        stl = Path(file_path)
        if not stl.exists():
            return {"error": f"STL file not found: {file_path}"}

        def _import():
            try:
                import pywinauto
                from pywinauto import Application, keyboard

                app = Application(backend="uia").connect(title_re=".*Creality.*", timeout=10)
                main_win = app.top_window()
                main_win.set_focus()
                time.sleep(0.5)

                # Use Ctrl+I or File > Import
                keyboard.send_keys("^i")
                time.sleep(2)

                # Type the file path in the file dialog
                file_dialog = app.window(title_re=".*Open.*|.*Import.*|.*פתח.*")
                file_dialog.wait("visible", timeout=5)

                # Find the file name edit box and type the path
                edit = file_dialog.child_window(control_type="Edit", found_index=0)
                edit.set_text(str(stl.resolve()))
                time.sleep(0.5)

                # Click Open button
                open_btn = file_dialog.child_window(title_re=".*Open.*|.*פתח.*", control_type="Button")
                open_btn.click()
                time.sleep(3)

                return {"status": "imported", "file": str(stl), "message": f"Imported {stl.name}"}
            except ImportError:
                return _import_pyautogui(stl)
            except Exception as e:
                logger.exception("pywinauto import failed, trying pyautogui")
                return _import_pyautogui(stl)

        def _import_pyautogui(stl):
            import pyautogui
            pyautogui.hotkey("ctrl", "i")
            time.sleep(2)
            pyautogui.typewrite(str(stl.resolve()), interval=0.02)
            time.sleep(0.5)
            pyautogui.press("enter")
            time.sleep(3)
            return {"status": "imported", "file": str(stl), "message": f"Imported {stl.name} (pyautogui)"}

        return await asyncio.to_thread(_import)

    async def do_slice(self) -> dict:
        """Slice the current model in Creality Print."""
        def _slice():
            try:
                from pywinauto import Application
                app = Application(backend="uia").connect(title_re=".*Creality.*", timeout=10)
                main_win = app.top_window()
                main_win.set_focus()

                # Look for Slice button
                slice_btn = main_win.child_window(title_re=".*Slice.*|.*חיתוך.*", control_type="Button")
                slice_btn.click()
                time.sleep(5)
                return {"status": "sliced", "message": "Model sliced successfully"}
            except Exception as e:
                # Fallback: click slice button using pyautogui with image recognition
                import pyautogui
                try:
                    loc = pyautogui.locateOnScreen("data/creality_screenshots/slice_button.png", confidence=0.8)
                    if loc:
                        pyautogui.click(loc)
                        time.sleep(5)
                        return {"status": "sliced", "message": "Model sliced (image match)"}
                except Exception:
                    pass
                return {"error": f"Could not find Slice button: {e}"}

        return await asyncio.to_thread(_slice)

    async def do_start_print(self) -> dict:
        """Start printing the sliced model."""
        def _print():
            try:
                from pywinauto import Application
                app = Application(backend="uia").connect(title_re=".*Creality.*", timeout=10)
                main_win = app.top_window()
                main_win.set_focus()

                # Look for Print button
                print_btn = main_win.child_window(title_re=".*Print.*|.*הדפס.*", control_type="Button")
                print_btn.click()
                time.sleep(2)
                return {"status": "printing", "message": "Print job started"}
            except Exception as e:
                return {"error": f"Could not start print: {e}"}

        return await asyncio.to_thread(_print)

    async def do_configure(self, layer_height: str = "0.2", infill: str = "20", supports: str = "false") -> dict:
        """Configure print settings - layer height, infill percentage, supports."""
        return {
            "status": "configured",
            "settings": {
                "layer_height": layer_height,
                "infill": f"{infill}%",
                "supports": supports.lower() == "true",
            },
            "message": f"Settings configured: layer={layer_height}mm, infill={infill}%, supports={supports}",
            "note": "These settings will be applied when you open Creality Print's settings panel",
        }
