"""Live RuView monitor — shows what RuView sees in real-time.

Run:  python scripts/ruview_live.py
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

RUVIEW_URL = os.environ.get("JARVIS_RUVIEW_URL", "http://localhost:3000")
POLL_INTERVAL = 2.0  # seconds


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def bar(value: float, max_val: float = 1.0, width: int = 20) -> str:
    filled = int((value / max_val) * width) if max_val else 0
    return f"[{'#' * filled}{'.' * (width - filled)}]"


async def fetch(path: str) -> dict | None:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{RUVIEW_URL}{path}", timeout=5)
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


async def main():
    print(f"Connecting to RuView at {RUVIEW_URL} ...")

    health = await fetch("/health")
    if not health:
        print(f"ERROR: Cannot reach RuView at {RUVIEW_URL}")
        print("Make sure the sensing server is running (docker compose up -d)")
        return

    print(f"Connected! Source: {health.get('source')}, Status: {health.get('status')}")
    print(f"Refreshing every {POLL_INTERVAL}s. Press Ctrl+C to stop.\n")
    await asyncio.sleep(1)

    try:
        while True:
            sensing = await fetch("/api/v1/sensing/latest")
            vitals = await fetch("/api/v1/vital-signs")

            clear()
            print("=" * 60)
            print("  RUVIEW LIVE MONITOR  ".center(60))
            print("=" * 60)

            if not sensing:
                print("\n  [OFFLINE] Cannot reach RuView API\n")
                await asyncio.sleep(POLL_INTERVAL)
                continue

            # --- Presence ---
            cls = sensing.get("classification", {})
            presence = cls.get("presence", False)
            motion = cls.get("motion_level", "unknown")
            confidence = cls.get("confidence", 0)
            estimated = sensing.get("estimated_persons", 0)

            print(f"\n  PRESENCE:  {'YES' if presence else 'NO'}")
            print(f"  People:    {estimated}")
            print(f"  Motion:    {motion}")
            print(f"  Confidence:{confidence:.1%}  {bar(confidence)}")

            # --- Vital Signs ---
            vs = (vitals or {}).get("vital_signs", sensing.get("vital_signs", {}))
            hr = vs.get("heart_rate_bpm", 0)
            br = vs.get("breathing_rate_bpm", 0)
            hr_conf = vs.get("heartbeat_confidence", 0)
            br_conf = vs.get("breathing_confidence", 0)
            quality = vs.get("signal_quality", 0)

            print(f"\n  VITAL SIGNS:")
            print(f"  Heart Rate:     {hr:5.1f} BPM   conf: {hr_conf:.0%}  {bar(hr_conf)}")
            print(f"  Breathing Rate: {br:5.1f} /min  conf: {br_conf:.0%}  {bar(br_conf)}")
            print(f"  Signal Quality: {quality:.0%}  {bar(quality)}")

            # --- Persons / Pose ---
            persons = sensing.get("persons", [])
            print(f"\n  PERSONS: {len(persons)}")
            for p in persons:
                pid = p.get("id", "?")
                zone = p.get("zone", "?")
                conf = p.get("confidence", 0)
                kps = p.get("keypoints", [])
                bbox = p.get("bbox", {})

                print(f"\n  Person #{pid}  zone={zone}  conf={conf:.1%}")
                if bbox:
                    print(f"    BBox: x={bbox.get('x',0):.0f} y={bbox.get('y',0):.0f} "
                          f"w={bbox.get('width',0):.0f} h={bbox.get('height',0):.0f}")

                # Show key body parts
                key_parts = ["nose", "left_shoulder", "right_shoulder",
                             "left_hip", "right_hip", "left_ankle", "right_ankle"]
                shown = [k for k in kps if k.get("name") in key_parts]
                if shown:
                    print(f"    Keypoints ({len(kps)} total):")
                    for k in shown:
                        print(f"      {k['name']:16s}  x={k.get('x',0):6.1f}  y={k.get('y',0):6.1f}  conf={k.get('confidence',0):.0%}")

            # --- Signal info ---
            features = sensing.get("features", {})
            nodes = sensing.get("nodes", [])
            print(f"\n  SIGNAL:")
            print(f"    Nodes: {len(nodes)}")
            print(f"    RSSI:  {features.get('mean_rssi', 0):.0f} dBm")
            print(f"    Dominant freq: {features.get('dominant_freq_hz', 0):.1f} Hz")
            print(f"    Variance: {features.get('variance', 0):.2f}")

            tick = sensing.get("tick", 0)
            source = sensing.get("source", "?")
            print(f"\n  Source: {source}  |  Tick: {tick}")
            print("=" * 60)
            print("  Press Ctrl+C to stop")

            await asyncio.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    asyncio.run(main())
