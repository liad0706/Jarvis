#!/usr/bin/env python3
"""Advanced WiFi Sensing Bridge v2 — RSSI fingerprinting + variance-based motion detection.

Maximizes accuracy from RSSI-only data by:
1. Tracking RSSI from multiple APs over time (fingerprinting)
2. Computing RSSI variance to detect human motion (body absorbs/reflects WiFi)
3. Encoding variance + multi-AP data into synthetic CSI I/Q format
4. Running continuous scans with background thread
"""

import subprocess
import struct
import socket
import time
import math
import random
import logging
import sys
import threading
from collections import deque

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

MAGIC = 0xC5110001
N_SUBCARRIERS = 56
HISTORY_SIZE = 20  # Keep last 20 readings per AP
SCAN_INTERVAL = 0.1  # Minimal delay between scans


class APTracker:
    """Track RSSI history for a single access point."""
    def __init__(self, bssid, freq_mhz=2437, ssid=""):
        self.bssid = bssid
        self.freq_mhz = freq_mhz
        self.ssid = ssid
        self.rssi_history = deque(maxlen=HISTORY_SIZE)
        self.last_seen = 0.0

    def add_reading(self, rssi, timestamp):
        self.rssi_history.append((rssi, timestamp))
        self.last_seen = timestamp

    @property
    def current_rssi(self):
        if not self.rssi_history:
            return -100
        return self.rssi_history[-1][0]

    @property
    def mean_rssi(self):
        if not self.rssi_history:
            return -100
        return sum(r for r, _ in self.rssi_history) / len(self.rssi_history)

    @property
    def variance(self):
        """RSSI variance — key indicator of human motion.
        Static environment: variance < 1 dB
        Human moving nearby: variance 2-8 dB
        Active motion in path: variance > 8 dB
        """
        if len(self.rssi_history) < 3:
            return 0.0
        values = [r for r, _ in self.rssi_history]
        mean = sum(values) / len(values)
        return sum((v - mean) ** 2 for v in values) / len(values)

    @property
    def delta(self):
        """Recent RSSI change — sudden changes indicate movement."""
        if len(self.rssi_history) < 2:
            return 0.0
        recent = list(self.rssi_history)
        return recent[-1][0] - recent[-2][0]

    @property
    def trend(self):
        """RSSI trend over last N readings (positive = getting closer)."""
        if len(self.rssi_history) < 4:
            return 0.0
        values = [r for r, _ in self.rssi_history]
        n = len(values)
        half = n // 2
        first_half = sum(values[:half]) / half
        second_half = sum(values[half:]) / (n - half)
        return second_half - first_half


class WiFiSensingBridge:
    def __init__(self, target_host, target_port, node_id=1):
        self.target_host = target_host
        self.target_port = target_port
        self.node_id = node_id
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ap_trackers = {}  # bssid -> APTracker
        self.seq = 0
        self.sent = 0
        self.scan_lock = threading.Lock()
        self.latest_scan = []
        self.running = True

    def scan_wifi(self):
        """Scan WiFi networks using iw."""
        networks = []
        try:
            r = subprocess.run(
                ["sudo", "iw", "wlan0", "scan"],
                capture_output=True, text=True, timeout=15,
            )
            current = {}
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("BSS "):
                    if current.get("rssi"):
                        networks.append(current)
                    current = {"bssid": line.split("(")[0].replace("BSS ", "").strip()}
                elif "signal:" in line:
                    try:
                        current["rssi"] = int(float(line.split(":")[1].strip().split(" ")[0]))
                    except Exception:
                        pass
                elif "freq:" in line:
                    try:
                        current["freq_mhz"] = int(line.split(":")[1].strip())
                    except Exception:
                        pass
                elif "SSID:" in line:
                    current["ssid"] = line.split(":", 1)[1].strip()
            if current.get("rssi"):
                networks.append(current)
        except Exception as e:
            logger.warning("scan failed: %s", e)
        return networks

    def update_trackers(self, networks):
        """Update AP trackers with new scan data."""
        now = time.time()
        for net in networks:
            bssid = net.get("bssid", "")
            if not bssid:
                continue
            if bssid not in self.ap_trackers:
                self.ap_trackers[bssid] = APTracker(
                    bssid,
                    net.get("freq_mhz", 2437),
                    net.get("ssid", ""),
                )
            self.ap_trackers[bssid].add_reading(net.get("rssi", -100), now)
            if net.get("freq_mhz"):
                self.ap_trackers[bssid].freq_mhz = net["freq_mhz"]

    def compute_motion_score(self):
        """Compute overall motion score from all AP variances.
        Returns 0-100 where:
          0-10: no motion (empty room)
          10-30: slight motion (someone sitting, breathing)
          30-60: moderate motion (walking)
          60-100: active motion (running, exercising)
        """
        now = time.time()
        active_aps = [
            ap for ap in self.ap_trackers.values()
            if now - ap.last_seen < 30 and len(ap.rssi_history) >= 3
        ]
        if not active_aps:
            return 0.0

        total_var = 0.0
        total_weight = 0.0
        max_delta = 0.0

        for ap in active_aps:
            weight = max(0.1, (ap.current_rssi + 100) / 60)
            total_var += ap.variance * weight
            total_weight += weight
            max_delta = max(max_delta, abs(ap.delta))

        avg_var = total_var / total_weight if total_weight > 0 else 0
        score = min(100, avg_var * 8 + max_delta * 3)
        return score

    def build_enhanced_iq(self, ap):
        """Build I/Q data that encodes RSSI + variance + motion info.

        RuView computes amplitude = sqrt(I^2 + Q^2) from signed int8 pairs.
        We need I/Q values with LARGE amplitude (40-100 range) for RuView
        to detect presence properly.

        Structure:
        - Subcarriers 0-19: Base signal — RSSI-scaled amplitude, stable phase
        - Subcarriers 20-39: Motion region — variance modulates amplitude
        - Subcarriers 40-55: Trend/delta — encodes movement direction
        """
        rssi = ap.current_rssi
        variance = ap.variance
        delta = ap.delta
        trend = ap.trend

        # Map RSSI to strong amplitude (40-110 range)
        # RSSI -30 dBm (strong) -> amp 100
        # RSSI -50 dBm (medium) -> amp 70
        # RSSI -80 dBm (weak)   -> amp 40
        base_amp = max(40, min(110, int(100 + (rssi + 40) * 1.4)))

        # Variance creates amplitude modulation
        var_mod = min(30, math.sqrt(variance) * 3)
        delta_phase = delta * 0.15

        iq = bytearray()
        for k in range(N_SUBCARRIERS):
            if k < 20:
                # Base signal — stable, high amplitude
                amp = base_amp + random.gauss(0, 3)
                phase = k * 0.3 + random.gauss(0, 0.05)
            elif k < 40:
                # Motion region — variance modulates
                amp = base_amp + random.gauss(0, var_mod + 5)
                phase = k * 0.3 + delta_phase + random.gauss(0, 0.1 + variance * 0.02)
            else:
                # Trend region
                amp = base_amp + trend * 3 + random.gauss(0, 4)
                phase = k * 0.3 + trend * 0.2 + random.gauss(0, 0.1)

            amp = max(10, min(120, int(amp)))
            i_val = int(amp * math.cos(phase))
            q_val = int(amp * math.sin(phase))
            # Clamp to signed int8 range
            i_val = max(-127, min(127, i_val))
            q_val = max(-127, min(127, q_val))
            iq.append(i_val & 0xFF)
            iq.append(q_val & 0xFF)

        return bytes(iq)

    def build_frame(self, ap):
        """Build RuView-compatible UDP frame."""
        iq_data = self.build_enhanced_iq(ap)
        h = struct.pack("<I", MAGIC)
        h += struct.pack("<B", self.node_id)
        h += struct.pack("<B", 1)  # n_antennas
        h += struct.pack("<H", N_SUBCARRIERS)
        h += struct.pack("<I", ap.freq_mhz)
        h += struct.pack("<I", self.seq)
        h += struct.pack("<b", max(-128, min(127, ap.current_rssi)))
        h += struct.pack("<b", -95)  # noise floor
        h += struct.pack("<H", 0)  # reserved
        return h + iq_data

    def scanner_thread(self):
        """Background scanner — runs continuously."""
        while self.running:
            networks = self.scan_wifi()
            if networks:
                with self.scan_lock:
                    self.latest_scan = networks
                    self.update_trackers(networks)
            time.sleep(SCAN_INTERVAL)

    def run(self):
        """Main sending loop."""
        logger.info("Advanced WiFi Sensing Bridge v2")
        logger.info("Target: %s:%d (node_id=%d)", self.target_host, self.target_port, self.node_id)
        logger.info("Features: multi-AP tracking, variance motion detection, enhanced I/Q")

        # Start background scanner
        scanner = threading.Thread(target=self.scanner_thread, daemon=True)
        scanner.start()
        logger.info("Background scanner started")

        # Wait for first scan
        time.sleep(3)

        while self.running:
            now = time.time()
            active_aps = sorted(
                [ap for ap in self.ap_trackers.values() if now - ap.last_seen < 30],
                key=lambda a: a.current_rssi,
                reverse=True,
            )

            if not active_aps:
                time.sleep(1)
                continue

            # Send frames for top APs
            for ap in active_aps[:10]:
                frame = self.build_frame(ap)
                self.sock.sendto(frame, (self.target_host, self.target_port))
                self.seq += 1
                self.sent += 1

            # Log stats
            motion = self.compute_motion_score()
            if self.sent % 50 == 0:
                n_tracked = len([a for a in self.ap_trackers.values() if now - a.last_seen < 30])
                top_var = max((a.variance for a in active_aps[:5]), default=0)
                logger.info(
                    "Sent %d | APs: %d | Motion: %.0f%% | TopVar: %.1f | Best RSSI: %d dBm",
                    self.sent, n_tracked, motion, top_var,
                    active_aps[0].current_rssi if active_aps else -100,
                )

            # ~20 Hz send rate
            time.sleep(0.05)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "10.0.0.22:5006"
    host, port = target.rsplit(":", 1)
    bridge = WiFiSensingBridge(host, int(port), node_id=1)
    try:
        bridge.run()
    except KeyboardInterrupt:
        bridge.running = False
        logger.info("Stopped.")
