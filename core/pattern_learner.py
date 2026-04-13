"""Pattern Learning engine — detects recurring user habits over time.

Analyses action history to find time-based, day-based, sequential, and
frequency patterns.  No ML — just counting and grouping.  Patterns are
persisted to data/patterns.json; raw events to data/pattern_events.json
(rolling 30-day window).
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent
_PATTERNS_PATH = _BASE_DIR / "data" / "patterns.json"
_EVENTS_PATH = _BASE_DIR / "data" / "pattern_events.json"

_EVENT_RETENTION_DAYS = 30
_MIN_CONFIDENCE_KEEP = 0.3


class PatternLearner:
    """Learns behavioural patterns from recorded events."""

    def __init__(self) -> None:
        self.patterns: list[dict[str, Any]] = []
        self.events: dict[str, list[dict[str, Any]]] = {}  # date_str -> [event, ...]
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if _PATTERNS_PATH.exists():
            try:
                self.patterns = json.loads(_PATTERNS_PATH.read_text(encoding="utf-8"))
                # Ensure confidence is always a float (JSON may store as string)
                for p in self.patterns:
                    if "confidence" in p:
                        p["confidence"] = float(p["confidence"])
            except Exception as exc:
                logger.warning("Failed to load patterns: %s", exc)
                self.patterns = []

        if _EVENTS_PATH.exists():
            try:
                self.events = json.loads(_EVENTS_PATH.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to load events: %s", exc)
                self.events = {}

        self._prune_old_events()

    def _save(self) -> None:
        os.makedirs(_PATTERNS_PATH.parent, exist_ok=True)
        _PATTERNS_PATH.write_text(
            json.dumps(self.patterns, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _EVENTS_PATH.write_text(
            json.dumps(self.events, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _prune_old_events(self) -> None:
        """Remove events older than _EVENT_RETENTION_DAYS."""
        cutoff = (datetime.now() - timedelta(days=_EVENT_RETENTION_DAYS)).strftime("%Y-%m-%d")
        old_keys = [k for k in self.events if k < cutoff]
        for k in old_keys:
            del self.events[k]

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_event(
        self,
        event_type: str,
        details: dict[str, Any] | str | None = None,
        hour: int | None = None,
        weekday: int | None = None,
    ) -> None:
        """Record a user event for later pattern detection.

        Parameters
        ----------
        event_type:
            Short action identifier, e.g. ``"lights_off"``, ``"play_music"``.
        details:
            Arbitrary metadata dict (device name, song genre, …).
        hour:
            Hour of day (0-23).  Defaults to current hour.
        weekday:
            Day of week (0=Mon … 6=Sun).  Defaults to today.
        """
        now = datetime.now()
        if hour is None:
            hour = now.hour
        if weekday is None:
            weekday = now.weekday()

        date_str = now.strftime("%Y-%m-%d")

        event: dict[str, Any] = {
            "type": event_type,
            "details": details or {},
            "hour": hour,
            "weekday": weekday,
            "timestamp": now.isoformat(),
        }

        self.events.setdefault(date_str, []).append(event)
        self._prune_old_events()
        self._save()

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze(self) -> None:
        """Run pattern analysis on accumulated events and persist results."""
        all_events = self._flat_events()
        if not all_events:
            return

        total_days = max(len(self.events), 1)
        new_patterns: list[dict[str, Any]] = []

        new_patterns.extend(self._find_time_action_patterns(all_events, total_days))
        new_patterns.extend(self._find_day_preference_patterns(all_events, total_days))
        new_patterns.extend(self._find_sequence_patterns(all_events, total_days))
        new_patterns.extend(self._find_frequency_patterns(all_events, total_days))

        # Merge with existing — update occurrences / confidence for known patterns,
        # add new ones, drop those below threshold.
        merged = self._merge_patterns(new_patterns)
        self.patterns = [p for p in merged if p["confidence"] >= _MIN_CONFIDENCE_KEEP]
        self._save()
        logger.info("Pattern analysis complete: %d patterns retained.", len(self.patterns))

    # -- helpers --

    def _flat_events(self) -> list[dict[str, Any]]:
        flat: list[dict[str, Any]] = []
        for day_events in self.events.values():
            flat.extend(day_events)
        return flat

    def _find_time_action_patterns(
        self, events: list[dict], total_days: int
    ) -> list[dict[str, Any]]:
        """Group events by (type, hour) to find time_action patterns."""
        counter: dict[tuple[str, int], int] = defaultdict(int)
        details_sample: dict[tuple[str, int], dict] = {}
        for ev in events:
            key = (ev["type"], ev["hour"])
            counter[key] += 1
            details_sample.setdefault(key, ev.get("details", {}))

        patterns: list[dict[str, Any]] = []
        for (etype, hour), count in counter.items():
            confidence = min(count / total_days, 1.0)
            patterns.append(
                self._make_pattern(
                    pattern_type="time_action",
                    description=f"המשתמש בדרך כלל מבצע {etype} בסביבות {hour:02d}:00",
                    confidence=confidence,
                    occurrences=count,
                    action_details={"event_type": etype, "hour": hour, "sample": details_sample.get((etype, hour), "")},
                )
            )
        return patterns

    def _find_day_preference_patterns(
        self, events: list[dict], total_days: int
    ) -> list[dict[str, Any]]:
        """Group events by (type, weekday) and look for day skew."""
        _day_names_he = ["שני", "שלישי", "רביעי", "חמישי", "חמישי", "שישי", "שבת"]
        counter: dict[tuple[str, int], int] = defaultdict(int)
        type_total: dict[str, int] = defaultdict(int)
        details_sample: dict[tuple[str, int], dict] = {}

        for ev in events:
            key = (ev["type"], ev["weekday"])
            counter[key] += 1
            type_total[ev["type"]] += 1
            details_sample.setdefault(key, ev.get("details", {}))

        patterns: list[dict[str, Any]] = []
        for (etype, weekday), count in counter.items():
            # Only flag if this day accounts for a disproportionate share
            expected = type_total[etype] / 7.0
            if count <= expected:
                continue
            days_of_weekday = sum(1 for d in self.events if _weekday_of(d) == weekday) or 1
            confidence = min(count / days_of_weekday, 1.0)
            day_name = _day_names_he[weekday] if weekday < len(_day_names_he) else str(weekday)
            det = details_sample.get((etype, weekday), "")
            detail_str = det if isinstance(det, str) else (det.get("genre") or det.get("name") or etype) if isinstance(det, dict) else etype
            patterns.append(
                self._make_pattern(
                    pattern_type="day_preference",
                    description=f"ביום {day_name} המשתמש מעדיף {detail_str}",
                    confidence=confidence,
                    occurrences=count,
                    action_details={"event_type": etype, "weekday": weekday, "sample": det},
                )
            )
        return patterns

    def _find_sequence_patterns(
        self, events: list[dict], total_days: int
    ) -> list[dict[str, Any]]:
        """Look at sequential event pairs within each day."""
        pair_counter: dict[tuple[str, str], int] = defaultdict(int)
        first_counter: dict[str, int] = defaultdict(int)

        for day_events in self.events.values():
            sorted_ev = sorted(day_events, key=lambda e: e.get("timestamp", ""))
            for i in range(len(sorted_ev) - 1):
                a = sorted_ev[i]["type"]
                b = sorted_ev[i + 1]["type"]
                if a == b:
                    continue
                pair_counter[(a, b)] += 1
                first_counter[a] += 1

        patterns: list[dict[str, Any]] = []
        for (a, b), count in pair_counter.items():
            if first_counter[a] == 0:
                continue
            confidence = min(count / max(first_counter[a], 1), 1.0)
            patterns.append(
                self._make_pattern(
                    pattern_type="sequence",
                    description=f"אחרי {a} המשתמש בדרך כלל מבצע {b}",
                    confidence=confidence,
                    occurrences=count,
                    action_details={"first": a, "then": b},
                )
            )
        return patterns

    def _find_frequency_patterns(
        self, events: list[dict], total_days: int
    ) -> list[dict[str, Any]]:
        """Count how often each event type happens per day."""
        daily_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for date_str, day_events in self.events.items():
            for ev in day_events:
                daily_counts[ev["type"]][date_str] += 1

        patterns: list[dict[str, Any]] = []
        for etype, day_map in daily_counts.items():
            total_count = sum(day_map.values())
            avg_per_day = total_count / total_days
            active_days = len(day_map)
            confidence = min(active_days / total_days, 1.0)
            if avg_per_day >= 1:
                freq_str = f"{avg_per_day:.1f} פעמים ביום"
            else:
                per_week = avg_per_day * 7
                freq_str = f"{per_week:.1f} פעמים בשבוע"
            patterns.append(
                self._make_pattern(
                    pattern_type="frequency",
                    description=f"המשתמש מבצע {etype} בערך {freq_str}",
                    confidence=confidence,
                    occurrences=total_count,
                    action_details={"event_type": etype, "avg_per_day": round(avg_per_day, 2)},
                )
            )
        return patterns

    # -- pattern CRUD helpers --

    @staticmethod
    def _make_pattern(
        pattern_type: str,
        description: str,
        confidence: float,
        occurrences: int,
        action_details: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "pattern_type": pattern_type,
            "description": description,
            "confidence": round(confidence, 3),
            "occurrences": occurrences,
            "last_seen": datetime.now().isoformat(),
            "action_details": action_details,
        }

    def _merge_patterns(self, new_patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge *new_patterns* into self.patterns by matching on
        (pattern_type, action_details).  New data wins for confidence /
        occurrences / last_seen; description is updated too.
        """
        index: dict[str, dict[str, Any]] = {}
        for p in self.patterns:
            key = self._pattern_key(p)
            index[key] = p

        for p in new_patterns:
            key = self._pattern_key(p)
            if key in index:
                existing = index[key]
                existing["confidence"] = p["confidence"]
                existing["occurrences"] = p["occurrences"]
                existing["last_seen"] = p["last_seen"]
                existing["description"] = p["description"]
            else:
                index[key] = p

        return list(index.values())

    @staticmethod
    def _pattern_key(p: dict[str, Any]) -> str:
        ad = p.get("action_details", {})
        return json.dumps({"t": p["pattern_type"], "d": ad}, sort_keys=True, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_patterns(self, min_confidence: float = 0.5) -> list[dict[str, Any]]:
        """Return patterns whose confidence meets *min_confidence*."""
        return [p for p in self.patterns if p["confidence"] >= min_confidence]

    def format_for_prompt(self, min_confidence: float = 0.5) -> str:
        """Return a concise Hebrew summary suitable for injection into an LLM
        prompt."""
        eligible = self.get_patterns(min_confidence)
        if not eligible:
            return ""

        lines = ["=== דפוסים שלמדתי ==="]
        for p in sorted(eligible, key=lambda x: -x["confidence"]):
            pct = int(p["confidence"] * 100)
            lines.append(f"• {p['description']} (ביטחון: {pct}%)")
        return "\n".join(lines)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _weekday_of(date_str: str) -> int:
    """Return weekday (0=Mon) for a YYYY-MM-DD string."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").weekday()
    except ValueError:
        return -1
