#!/usr/bin/env python3
"""Raspberry Pi CSI → RuView Bridge

Captures real WiFi CSI data from a Raspberry Pi (using Nexmon CSI)
and sends it to the RuView sensing server via UDP on port 5005.

=== SETUP ON RASPBERRY PI ===

1. Install Nexmon CSI (one-time):
   git clone https://github.com/seemoo-lab/nexmon_csi.git
   cd nexmon_csi
   # Follow instructions for your Pi model:
   # Pi 4:  make install-firmware KERNEL=5.10
   # Pi 5:  check https://github.com/seemoo-lab/nexmon_csi for latest support

2. Enable monitor mode:
   sudo ifconfig wlan0 up
   sudo nexutil -Iwlan0 -s500 -b -l34 -v$(python3 -c "
   import struct
   # channel, bandwidth, num_packets (0=infinite), core_mask, spatial_mask, ...
   print(struct.pack('<HHHHBBBBBBBB', 6, 0x1001, 0, 0xFFFF, 1, 1, 0,0,0,0,0,0).hex())
   ")
   sudo iw dev wlan0 interface add mon0 type monitor
   sudo ifconfig mon0 up

3. Run this bridge:
   python3 rpi_csi_bridge.py --target 10.0.0.XX:5005

   (Replace 10.0.0.XX with your PC's IP where RuView Docker runs)

=== ALTERNATIVE: No Nexmon (RSSI-only fallback) ===

If Nexmon doesn't work on your Pi model, this script falls back to
scanning WiFi networks and synthesizing frames from RSSI data.
Less accurate but still gives basic presence detection.

   python3 rpi_csi_bridge.py --target 10.0.0.XX:5005 --mode rssi
"""

import argparse
import socket
import struct
import time
import sys
import os
import subprocess
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# RuView UDP frame format (ADR-018)
MAGIC = 0xC511_0001
HEADER_SIZE = 20


def build_csi_frame(
    node_id: int,
    n_subcarriers: int,
    freq_mhz: int,
    sequence: int,
    rssi: int,
    noise_floor: int,
    iq_data: bytes,
) -> bytes:
    """Build a RuView-compatible UDP frame from CSI data."""
    header = struct.pack(
        "<IBBHHIIB1xH",
        MAGIC,           # 4 bytes: magic
        node_id,         # 1 byte: node ID
        1,               # 1 byte: n_antennas
        n_subcarriers,   # 2 bytes: n_subcarriers
        freq_mhz,        # 4 bytes: frequency MHz (NOTE: this packs wrong, see below)
        sequence,        # 4 bytes: sequence number
        rssi & 0xFF,     # 1 byte: RSSI (signed)
        noise_floor & 0xFF,  # 1 byte: noise floor
        0,               # 2 bytes: reserved
    )
    # Fix: pack header manually for correct layout
    header = struct.pack("<I", MAGIC)                    # 0-3: magic
    header += struct.pack("<B", node_id)                 # 4: node_id
    header += struct.pack("<B", 1)                       # 5: n_antennas
    header += struct.pack("<H", n_subcarriers)           # 6-7: n_subcarriers
    header += struct.pack("<I", freq_mhz)                # 8-11: freq_mhz
    header += struct.pack("<I", sequence)                # 12-15: sequence
    header += struct.pack("<b", max(-128, min(127, rssi)))  # 16: rssi (signed)
    header += struct.pack("<b", max(-128, min(127, noise_floor)))  # 17: noise floor
    header += struct.pack("<H", 0)                       # 18-19: reserved

    assert len(header) == HEADER_SIZE
    return header + iq_data


# ------------------------------------------------------------------
# Mode 1: Real CSI via Nexmon
# ------------------------------------------------------------------

def check_nexmon():
    """Check if Nexmon CSI is available."""
    try:
        result = subprocess.run(["nexutil", "-v"], capture_output=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def capture_csi_nexmon(interface: str = "mon0"):
    """Capture CSI frames from Nexmon monitor interface using pcap."""
    try:
        import pcap  # python-libpcap
    except ImportError:
        try:
            from scapy.all import sniff, raw
            logger.info("Using scapy for CSI capture on %s", interface)
            return _capture_csi_scapy(interface)
        except ImportError:
            logger.error("Need either python-libpcap or scapy. Install: pip3 install scapy")
            sys.exit(1)

    logger.info("Capturing CSI from %s via libpcap", interface)
    pc = pcap.pcap(interface, snaplen=65535, promisc=True, timeout_ms=100)

    for timestamp, raw_pkt in pc:
        # Nexmon CSI frames have a specific format in the pcap payload
        # The CSI data starts after the radiotap + 802.11 headers
        # Typical offset: ~42 bytes for radiotap + UDP-like Nexmon header
        if len(raw_pkt) < 60:
            continue

        # Extract Nexmon CSI payload
        # Format depends on Nexmon version, but typically:
        # - 4 bytes: magic (0x11111111)
        # - 2 bytes: RSSI
        # - 2 bytes: frame control
        # - 6 bytes: source MAC
        # - 2 bytes: sequence number
        # - 2 bytes: core/spatial stream
        # - 2 bytes: chanspec
        # - 2 bytes: chip version
        # - N*4 bytes: CSI data (I/Q as int16 pairs)

        yield _parse_nexmon_csi(raw_pkt)


def _capture_csi_scapy(interface: str):
    """Capture CSI using scapy."""
    from scapy.all import sniff, raw

    def process_packet(pkt):
        raw_bytes = raw(pkt)
        if len(raw_bytes) > 60:
            return _parse_nexmon_csi(raw_bytes)
        return None

    # Use callback-based sniffing
    packets = sniff(iface=interface, count=1, timeout=1)
    for pkt in packets:
        result = process_packet(pkt)
        if result:
            yield result


def _parse_nexmon_csi(raw_pkt: bytes) -> dict | None:
    """Parse Nexmon CSI frame from raw pcap packet."""
    # Find Nexmon magic in the packet
    magic_offset = raw_pkt.find(b'\x11\x11\x11\x11')
    if magic_offset < 0:
        return None

    data = raw_pkt[magic_offset:]
    if len(data) < 24:
        return None

    try:
        magic = struct.unpack_from("<I", data, 0)[0]
        rssi = struct.unpack_from("<h", data, 4)[0]
        seq = struct.unpack_from("<H", data, 14)[0]
        chanspec = struct.unpack_from("<H", data, 18)[0]

        # Channel from chanspec
        channel = chanspec & 0xFF
        if channel <= 14:
            freq_mhz = 2412 + (channel - 1) * 5
        else:
            freq_mhz = 5000 + channel * 5

        # CSI I/Q data starts at offset 24
        csi_raw = data[24:]
        n_subcarriers = len(csi_raw) // 4  # Each subcarrier: 2 bytes I + 2 bytes Q

        # Convert Nexmon int16 I/Q to RuView int8 I/Q format
        iq_data = bytearray()
        for i in range(n_subcarriers):
            offset = i * 4
            if offset + 3 >= len(csi_raw):
                break
            i_val = struct.unpack_from("<h", csi_raw, offset)[0]
            q_val = struct.unpack_from("<h", csi_raw, offset + 2)[0]
            # Scale int16 → int8
            i8 = max(-128, min(127, i_val >> 8))
            q8 = max(-128, min(127, q_val >> 8))
            iq_data.append(i8 & 0xFF)
            iq_data.append(q8 & 0xFF)

        return {
            "rssi": rssi,
            "freq_mhz": freq_mhz,
            "sequence": seq,
            "n_subcarriers": min(n_subcarriers, 56),
            "iq_data": bytes(iq_data[:56 * 2]),  # Cap at 56 subcarriers
        }
    except Exception as e:
        logger.debug("CSI parse error: %s", e)
        return None


# ------------------------------------------------------------------
# Mode 2: RSSI fallback (no Nexmon needed)
# ------------------------------------------------------------------

def scan_wifi_rssi() -> list[dict]:
    """Scan visible WiFi networks and return RSSI values."""
    networks = []
    try:
        result = subprocess.run(
            ["iwlist", "wlan0", "scan"],
            capture_output=True, text=True, timeout=10,
        )
        current = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if "Cell" in line and "Address" in line:
                if current:
                    networks.append(current)
                current = {"bssid": line.split("Address:")[1].strip()}
            elif "Signal level" in line:
                # Signal level=-45 dBm
                parts = line.split("Signal level=")
                if len(parts) > 1:
                    sig = parts[1].split(" ")[0].replace("dBm", "")
                    try:
                        current["rssi"] = int(sig)
                    except ValueError:
                        pass
            elif "Frequency:" in line:
                parts = line.split("Frequency:")
                if len(parts) > 1:
                    freq_str = parts[1].split(" ")[0]
                    try:
                        current["freq_mhz"] = int(float(freq_str) * 1000)
                    except ValueError:
                        pass
            elif "Channel:" in line:
                parts = line.split("Channel:")
                if len(parts) > 1:
                    try:
                        ch = int(parts[1].strip())
                        if ch <= 14:
                            current["freq_mhz"] = 2412 + (ch - 1) * 5
                        else:
                            current["freq_mhz"] = 5000 + ch * 5
                    except ValueError:
                        pass

        if current:
            networks.append(current)
    except Exception as e:
        logger.warning("WiFi scan failed: %s", e)

    return networks


def synthesize_iq_from_rssi(rssi: int, n_subcarriers: int = 56) -> bytes:
    """Create synthetic I/Q data from RSSI (basic amplitude, no real phase)."""
    import math
    import random

    # Convert RSSI to amplitude scale
    amplitude = max(1, min(127, int(10 ** ((rssi + 30) / 20))))

    iq_data = bytearray()
    for k in range(n_subcarriers):
        # Add some frequency-dependent variation + noise
        phase = (k * 0.3) + random.gauss(0, 0.2)
        noise = random.gauss(0, amplitude * 0.1)
        i_val = int(amplitude * math.cos(phase) + noise)
        q_val = int(amplitude * math.sin(phase) + noise)
        i_val = max(-128, min(127, i_val))
        q_val = max(-128, min(127, q_val))
        iq_data.append(i_val & 0xFF)
        iq_data.append(q_val & 0xFF)

    return bytes(iq_data)


# ------------------------------------------------------------------
# Main bridge loop
# ------------------------------------------------------------------

def run_csi_bridge(target_host: str, target_port: int, node_id: int = 1):
    """Run the CSI bridge using Nexmon."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq = 0
    sent = 0

    logger.info("CSI Bridge → %s:%d (Nexmon mode, node_id=%d)", target_host, target_port, node_id)

    for csi in capture_csi_nexmon():
        if csi is None:
            continue

        frame = build_csi_frame(
            node_id=node_id,
            n_subcarriers=csi["n_subcarriers"],
            freq_mhz=csi["freq_mhz"],
            sequence=seq,
            rssi=csi["rssi"],
            noise_floor=-95,
            iq_data=csi["iq_data"],
        )

        sock.sendto(frame, (target_host, target_port))
        seq += 1
        sent += 1

        if sent % 100 == 0:
            logger.info("Sent %d CSI frames", sent)

        time.sleep(0.02)  # 50 Hz max


def run_rssi_bridge(target_host: str, target_port: int, node_id: int = 1):
    """Run the RSSI fallback bridge (no Nexmon needed)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq = 0
    sent = 0

    logger.info("RSSI Bridge → %s:%d (fallback mode, node_id=%d)", target_host, target_port, node_id)
    logger.info("Scanning WiFi every 0.5s...")

    while True:
        networks = scan_wifi_rssi()
        if not networks:
            time.sleep(1)
            continue

        # Use the strongest signal as primary
        networks.sort(key=lambda n: n.get("rssi", -100), reverse=True)

        for net in networks[:5]:  # Top 5 networks
            rssi = net.get("rssi", -60)
            freq = net.get("freq_mhz", 2437)
            iq_data = synthesize_iq_from_rssi(rssi)

            frame = build_csi_frame(
                node_id=node_id,
                n_subcarriers=56,
                freq_mhz=freq,
                sequence=seq,
                rssi=rssi,
                noise_floor=-95,
                iq_data=iq_data,
            )

            sock.sendto(frame, (target_host, target_port))
            seq += 1
            sent += 1

        if sent % 50 == 0:
            logger.info("Sent %d RSSI frames (%d networks visible)", sent, len(networks))

        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description="Raspberry Pi CSI → RuView Bridge")
    parser.add_argument("--target", default="10.0.0.1:5005",
                        help="RuView server address (default: 10.0.0.1:5005)")
    parser.add_argument("--mode", choices=["auto", "csi", "rssi"], default="auto",
                        help="CSI capture mode: auto (try Nexmon, fallback to RSSI), csi (Nexmon only), rssi (scan only)")
    parser.add_argument("--node-id", type=int, default=1,
                        help="Node ID for this sensor (default: 1)")

    args = parser.parse_args()

    host, port = args.target.rsplit(":", 1)
    port = int(port)

    if args.mode == "auto":
        if check_nexmon():
            logger.info("Nexmon CSI detected! Using real CSI mode.")
            run_csi_bridge(host, port, args.node_id)
        else:
            logger.info("Nexmon not found. Using RSSI fallback mode.")
            run_rssi_bridge(host, port, args.node_id)
    elif args.mode == "csi":
        run_csi_bridge(host, port, args.node_id)
    elif args.mode == "rssi":
        run_rssi_bridge(host, port, args.node_id)


if __name__ == "__main__":
    main()
