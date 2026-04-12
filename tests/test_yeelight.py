"""Quick test: discover Yeelight bulbs on the network."""

import io
import sys

from yeelight import discover_bulbs


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    print("Scanning for Yeelight devices...")
    bulbs = discover_bulbs(timeout=5)

    if bulbs:
        print(f"Found {len(bulbs)} Yeelight device(s):")
        for b in bulbs:
            ip = b.get("ip", "?")
            cap = b.get("capabilities", {})
            name = cap.get("name", "unnamed")
            model = cap.get("model", "?")
            power = cap.get("power", "?")
            bright = cap.get("bright", "?")
            print(f"  IP: {ip}")
            print(f"  Name: {name}")
            print(f"  Model: {model}")
            print(f"  Power: {power}")
            print(f"  Brightness: {bright}%")
            print()
    else:
        print("No Yeelight devices found.")
        print("Make sure:")
        print("  1. The bulb is powered on")
        print("  2. LAN Control is enabled in the Yeelight app")
        print("  3. The bulb is on the same Wi-Fi as this computer")


if __name__ == "__main__":
    main()
