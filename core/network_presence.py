"""Network Presence Detection — discovers who is home by scanning the LAN.

Checks known devices (phones etc.) against the local ARP table and, as a
fallback, pings each known IP.  Results are cached for two minutes so the
LLM can query presence cheaply on every turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KNOWN_DEVICES_PATH = PROJECT_ROOT / "data" / "known_devices.json"

# How long (seconds) a scan result stays valid before we re-scan.
_CACHE_TTL = 120  # 2 minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_mac(mac: str) -> str:
    """Normalise a MAC address to uppercase colon-separated form.

    Windows ARP output uses dashes (aa-bb-cc-dd-ee-ff) while the config
    file uses colons.  This ensures both compare equal.
    """
    return mac.strip().replace("-", ":").upper()


def _load_devices() -> list[dict[str, str]]:
    """Load the known-devices list from disk, creating the file if needed."""
    if not KNOWN_DEVICES_PATH.exists():
        KNOWN_DEVICES_PATH.parent.mkdir(parents=True, exist_ok=True)
        KNOWN_DEVICES_PATH.write_text("[]", encoding="utf-8")
        return []
    try:
        data = json.loads(KNOWN_DEVICES_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            logger.warning("known_devices.json is not a list — treating as empty")
            return []
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read known_devices.json: %s", exc)
        return []


def _save_devices(devices: list[dict[str, str]]) -> None:
    KNOWN_DEVICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    KNOWN_DEVICES_PATH.write_text(
        json.dumps(devices, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class NetworkPresence:
    """Detects which known people are on the local network."""

    def __init__(self) -> None:
        self._devices: list[dict[str, str]] = _load_devices()
        self._cache: dict[str, Any] | None = None
        self._cache_ts: float = 0.0
        self._arp_pairs: list[tuple[str, str]] = []

    # ---- public API -------------------------------------------------------

    async def scan(self) -> dict[str, list[str]]:
        """Return ``{"home": [...], "away": [...], "unknown": [...], "all_devices": [...]}``."""
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_ts) < _CACHE_TTL:
            return self._cache

        self._devices = _load_devices()

        # --- Try ARP table first -------------------------------------------
        found_macs, found_ips = await self._arp_scan()

        home_owners: set[str] = set()
        checked_owners: set[str] = set()
        known_macs: set[str] = set()
        known_ips: set[str] = set()

        for dev in self._devices:
            owner = dev.get("owner", "")
            checked_owners.add(owner)
            mac = _normalise_mac(dev.get("mac", ""))
            ip = dev.get("ip", "")
            known_macs.add(mac)
            known_ips.add(ip)
            if mac in found_macs or ip in found_ips:
                home_owners.add(owner)

        # --- Ping sweep for owners still not found -------------------------
        missing_devs = [
            d for d in self._devices if d.get("owner", "") not in home_owners
        ]
        if missing_devs:
            ping_results = await self._ping_sweep(missing_devs)
            for dev, alive in ping_results:
                if alive:
                    home_owners.add(dev.get("owner", ""))

        away_owners = checked_owners - home_owners

        # --- Collect ALL devices on network (known + unknown) -------------
        # Build list of all detected devices with their info
        all_devices: list[dict[str, str]] = []
        for ip_addr in sorted(found_ips):
            # Skip broadcast / multicast
            if ip_addr.endswith(".255") or ip_addr.startswith("224.") or ip_addr.startswith("239."):
                continue
            # Find matching MAC
            mac_for_ip = ""
            for m, i in self._arp_pairs:
                if i == ip_addr:
                    mac_for_ip = m
                    break
            # Check if this is a known device
            device_name = ""
            device_owner = ""
            for dev in self._devices:
                if _normalise_mac(dev.get("mac", "")) == mac_for_ip or dev.get("ip", "") == ip_addr:
                    device_name = dev.get("name", "")
                    device_owner = dev.get("owner", "")
                    break
            all_devices.append({
                "ip": ip_addr,
                "mac": mac_for_ip,
                "name": device_name or "לא ידוע",
                "owner": device_owner,
            })

        unknown_ips = [d["ip"] for d in all_devices if not d["owner"]]

        result = {
            "home": sorted(home_owners),
            "away": sorted(away_owners),
            "unknown": unknown_ips,
            "all_devices": all_devices,
            "total_connected": len(all_devices),
        }
        self._cache = result
        self._cache_ts = now
        logger.info(
            "Network presence scan: home=%s, away=%s, total=%d devices",
            result["home"], result["away"], len(all_devices),
        )
        return result

    async def is_home(self, owner: str) -> bool:
        """Check whether *owner* is currently on the network."""
        result = await self.scan()
        return owner.lower() in [o.lower() for o in result["home"]]

    def register_device(
        self, name: str, mac: str, ip: str, owner: str
    ) -> None:
        """Add (or update) a known device and persist to disk."""
        mac = _normalise_mac(mac)
        # Update existing entry if same MAC
        for dev in self._devices:
            if _normalise_mac(dev.get("mac", "")) == mac:
                dev.update({"name": name, "ip": ip, "owner": owner})
                _save_devices(self._devices)
                return
        self._devices.append(
            {"name": name, "mac": mac, "ip": ip, "owner": owner}
        )
        _save_devices(self._devices)

    def list_devices(self) -> list[dict[str, str]]:
        """Return the current known-devices list."""
        self._devices = _load_devices()
        return list(self._devices)

    @staticmethod
    def format_for_prompt(scan_result: dict) -> str:
        """Pretty-print scan results for the LLM system prompt (Hebrew)."""
        lines = []

        # Build a name-map from all known devices
        devices = _load_devices()
        owner_to_name: dict[str, str] = {}
        for dev in devices:
            o = dev.get("owner", "")
            if o and o not in owner_to_name:
                owner_to_name[o] = dev.get("name", o)

        # --- Known people status ---
        if scan_result.get("home") or scan_result.get("away"):
            lines.append("=== \u05de\u05d9 \u05d1\u05d1\u05d9\u05ea ===")
            for owner in scan_result.get("home", []):
                display = owner_to_name.get(owner, owner)
                lines.append(f"\u2022 {display} \u2014 \u05d1\u05d1\u05d9\u05ea \U0001f3e0")
            for owner in scan_result.get("away", []):
                display = owner_to_name.get(owner, owner)
                lines.append(f"\u2022 {display} \u2014 \u05dc\u05d0 \u05d1\u05d1\u05d9\u05ea")

        # --- All connected devices ---
        all_devices = scan_result.get("all_devices", [])
        if all_devices:
            total = scan_result.get("total_connected", len(all_devices))
            lines.append(f"\n=== \u05de\u05db\u05e9\u05d9\u05e8\u05d9\u05dd \u05de\u05d7\u05d5\u05d1\u05e8\u05d9\u05dd \u05dc\u05e8\u05e9\u05ea ({total}) ===")
            for d in all_devices:
                name = d.get("name", "\u05dc\u05d0 \u05d9\u05d3\u05d5\u05e2")
                ip = d.get("ip", "?")
                owner = d.get("owner", "")
                mac = d.get("mac", "")
                if owner:
                    lines.append(f"\u2022 {name} ({owner}) \u2014 {ip}")
                else:
                    short_mac = mac[-8:] if mac else "?"
                    lines.append(f"\u2022 \u05dc\u05d0 \u05d9\u05d3\u05d5\u05e2 ({short_mac}) \u2014 {ip}")

        if not lines:
            return "\u05dc\u05d0 \u05e0\u05de\u05e6\u05d0\u05d5 \u05de\u05db\u05e9\u05d9\u05e8\u05d9\u05dd \u05d1\u05e8\u05e9\u05ea."

        return "\n".join(lines)

    # ---- internal scanners ------------------------------------------------

    async def _arp_scan(self) -> tuple[set[str], set[str]]:
        """Run ``arp -a`` and return (set_of_macs, set_of_ips) found.

        Also populates ``self._arp_pairs`` with (mac, ip) tuples for
        later use by the full-device listing.
        """
        found_macs: set[str] = set()
        found_ips: set[str] = set()
        self._arp_pairs: list[tuple[str, str]] = []
        try:
            proc = await asyncio.create_subprocess_exec(
                "arp", "-a",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            text = stdout.decode("utf-8", errors="replace")

            # Windows arp -a output looks like:
            #   10.0.0.5     aa-bb-cc-dd-ee-ff     dynamic
            for line in text.splitlines():
                line = line.strip()
                m = re.match(
                    r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # IP
                    r"\s+"
                    r"([0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-]"
                    r"[0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-]"
                    r"[0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2})",
                    line,
                )
                if m:
                    ip = m.group(1)
                    mac = _normalise_mac(m.group(2))
                    found_ips.add(ip)
                    found_macs.add(mac)
                    self._arp_pairs.append((mac, ip))

            logger.debug("ARP scan found %d MACs, %d IPs", len(found_macs), len(found_ips))
        except Exception as exc:
            logger.warning("ARP scan failed: %s", exc)

        return found_macs, found_ips

    async def _ping_sweep(
        self, devices: list[dict[str, str]]
    ) -> list[tuple[dict[str, str], bool]]:
        """Ping each device IP once; return list of (device, alive)."""

        async def _ping_one(dev: dict[str, str]) -> tuple[dict[str, str], bool]:
            ip = dev.get("ip", "")
            if not ip:
                return dev, False
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ping", "-n", "1", "-w", "1000", ip,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=5)
                return dev, proc.returncode == 0
            except Exception as exc:
                logger.debug("Ping %s failed: %s", ip, exc)
                return dev, False

        results = await asyncio.gather(*[_ping_one(d) for d in devices])
        return list(results)

    # TODO: Home Assistant integration — query device_tracker entities via
    #       the HA REST API (http://<ha>:8123/api/states/device_tracker.*)
    #       when HA base URL + token are configured.
