"""Creality K1 Max direct REST API skill via Moonraker/Klipper."""

import logging
import os
from pathlib import Path

import httpx

from config import get_settings
from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 7125


def _printer_base_url() -> str:
    # Prefer the bare env var; fall back to JARVIS_CREALITY_PRINTER_IP via settings
    ip = os.environ.get("CREALITY_PRINTER_IP", "")
    if not ip:
        try:
            ip = get_settings().creality_printer_ip
        except Exception:
            pass
    if not ip:
        return ""
    return f"http://{ip}:{_DEFAULT_PORT}"


class CrealityAPISkill(BaseSkill):
    name = "creality_api"
    description = (
        "Control a Creality K1 Max 3D printer directly via its Moonraker REST API. "
        "Actions: status (temps, progress, ETA), pause, resume, cancel, "
        "get_camera_snapshot, upload_gcode, list_files. "
        "Requires CREALITY_PRINTER_IP environment variable."
    )

    RISK_MAP = {
        "status": "read",
        "list_files": "read",
        "get_camera_snapshot": "read",
        "upload_gcode": "write",
        "pause": "write",
        "resume": "write",
        "cancel": "critical",
    }

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("creality_api.%s failed", action)
            return {"error": str(e)}

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _base_url(self) -> str:
        return _printer_base_url()

    def _require_ip(self) -> str | None:
        url = self._base_url()
        if not url:
            return None
        return url

    async def _get(self, path: str, timeout: float = 10.0) -> dict:
        base = self._require_ip()
        if not base:
            return {"error": "CREALITY_PRINTER_IP is not set"}
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{base}{path}", timeout=timeout)
                r.raise_for_status()
                return r.json()
        except httpx.ConnectError:
            return {"error": "Cannot connect to printer — check that it is on and CREALITY_PRINTER_IP is correct"}
        except httpx.TimeoutException:
            return {"error": "Request to printer timed out"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}

    async def _post(self, path: str, data: dict | None = None, timeout: float = 10.0) -> dict:
        base = self._require_ip()
        if not base:
            return {"error": "CREALITY_PRINTER_IP is not set"}
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(f"{base}{path}", json=data or {}, timeout=timeout)
                r.raise_for_status()
                return r.json()
        except httpx.ConnectError:
            return {"error": "Cannot connect to printer — check that it is on and CREALITY_PRINTER_IP is correct"}
        except httpx.TimeoutException:
            return {"error": "Request to printer timed out"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    async def do_status(self) -> dict:
        """Get printer status: temperatures, print progress %, ETA, and printer state."""
        objects = (
            "heater_bed,extruder,print_stats,display_status,virtual_sdcard,toolhead"
        )
        result = await self._get(f"/printer/objects/query?{objects}")
        if "error" in result:
            return result

        status = result.get("result", {}).get("status", {})

        extruder = status.get("extruder", {})
        bed = status.get("heater_bed", {})
        print_stats = status.get("print_stats", {})
        display = status.get("display_status", {})
        vsd = status.get("virtual_sdcard", {})

        progress_pct = round(display.get("progress", vsd.get("progress", 0)) * 100, 1)
        total_duration = print_stats.get("total_duration", 0)
        print_duration = print_stats.get("print_duration", 0)

        eta_seconds: int | None = None
        if progress_pct > 0 and print_duration > 0:
            estimated_total = print_duration / (progress_pct / 100)
            eta_seconds = max(0, int(estimated_total - print_duration))

        return {
            "status": "ok",
            "printer_state": print_stats.get("state", "unknown"),
            "filename": print_stats.get("filename", ""),
            "progress_pct": progress_pct,
            "eta_seconds": eta_seconds,
            "extruder_temp": extruder.get("temperature"),
            "extruder_target": extruder.get("target"),
            "bed_temp": bed.get("temperature"),
            "bed_target": bed.get("target"),
            "total_duration_seconds": int(total_duration),
            "print_duration_seconds": int(print_duration),
        }

    async def do_pause(self) -> dict:
        """Pause the current print job."""
        result = await self._post("/printer/print/pause")
        if "error" in result:
            return result
        return {"status": "ok", "message": "Print paused"}

    async def do_resume(self) -> dict:
        """Resume a paused print job."""
        result = await self._post("/printer/print/resume")
        if "error" in result:
            return result
        return {"status": "ok", "message": "Print resumed"}

    async def do_cancel(self) -> dict:
        """Cancel the current print job."""
        result = await self._post("/printer/print/cancel")
        if "error" in result:
            return result
        return {"status": "ok", "message": "Print cancelled"}

    async def do_get_camera_snapshot(self) -> dict:
        """Fetch a JPEG snapshot from the printer's built-in camera."""
        base = self._require_ip()
        if not base:
            return {"error": "CREALITY_PRINTER_IP is not set"}

        # K1 Max exposes a MJPEG stream; Moonraker also proxies it via /webcam/?action=snapshot
        snapshot_paths = [
            "/webcam/?action=snapshot",
            "/webcam2/?action=snapshot",
            "/snapshot",
        ]

        for path in snapshot_paths:
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.get(f"{base}{path}", timeout=10.0)
                    if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                        return {
                            "status": "ok",
                            "content_type": r.headers.get("content-type"),
                            "size_bytes": len(r.content),
                            "image_bytes": r.content,
                            "url": f"{base}{path}",
                        }
            except (httpx.ConnectError, httpx.TimeoutException):
                return {"error": "Cannot connect to printer camera"}
            except Exception:
                continue

        return {"error": "Camera snapshot not available — check webcam is enabled in Moonraker config"}

    async def do_upload_gcode(self, file_path: str) -> dict:
        """Upload a local .gcode file to the printer's storage."""
        p = Path(file_path)
        if not p.exists():
            return {"error": f"File not found: {file_path}"}
        if not p.suffix.lower() in (".gcode", ".gc", ".g"):
            return {"error": f"Not a gcode file: {file_path}"}

        base = self._require_ip()
        if not base:
            return {"error": "CREALITY_PRINTER_IP is not set"}

        try:
            async with httpx.AsyncClient() as client:
                with p.open("rb") as fh:
                    r = await client.post(
                        f"{base}/server/files/upload",
                        files={"file": (p.name, fh, "application/octet-stream")},
                        timeout=120.0,
                    )
                r.raise_for_status()
                data = r.json()
                return {
                    "status": "ok",
                    "filename": data.get("item", {}).get("path", p.name),
                    "message": f"Uploaded {p.name} to printer",
                }
        except httpx.ConnectError:
            return {"error": "Cannot connect to printer"}
        except httpx.TimeoutException:
            return {"error": "Upload timed out — file may be too large or printer is slow"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}

    async def do_list_files(self) -> dict:
        """List gcode files stored on the printer."""
        result = await self._get("/server/files/list?root=gcodes")
        if "error" in result:
            return result

        files = result.get("result", [])
        return {
            "status": "ok",
            "count": len(files),
            "files": [
                {
                    "filename": f.get("filename") or f.get("path", ""),
                    "size_bytes": f.get("size"),
                    "modified": f.get("modified"),
                }
                for f in files
            ],
        }
