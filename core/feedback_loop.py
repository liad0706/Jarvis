"""
Feedback Loop System for Jarvis.

Tracks "action -> reaction" pairs so Jarvis can learn what works
and what doesn't for the user over time.
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from collections import Counter
from typing import Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
FEEDBACK_FILE = os.path.join(DATA_DIR, "feedback.json")
RETENTION_DAYS = 90


class FeedbackLoop:
    """Tracks Jarvis actions and user reactions to learn preferences."""

    def __init__(self, filepath: str = FEEDBACK_FILE):
        self.filepath = filepath
        self.entries: list[dict] = []
        self._load()

    # ------------------------------------------------------------------ #
    #  Persistence
    # ------------------------------------------------------------------ #

    def _load(self):
        """Load feedback entries from disk."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self.entries = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.entries = []
        else:
            self.entries = []

    def _save(self):
        """Save feedback entries to disk."""
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------ #
    #  Core recording
    # ------------------------------------------------------------------ #

    def record_action(self, action_type: str, action_detail: str) -> str:
        """Record a pending action and return its unique action_id."""
        action_id = uuid.uuid4().hex[:12]
        entry = {
            "action_id": action_id,
            "action_type": action_type,
            "action_detail": action_detail,
            "timestamp": datetime.now().isoformat(),
            "reaction": None,
            "reaction_signal": None,
        }
        self.entries.append(entry)
        self._save()
        return action_id

    def record_reaction(self, action_id: str, reaction: str, signal: str):
        """
        Update an existing action entry with the user's reaction.

        Parameters
        ----------
        action_id : str
            The id returned by ``record_action``.
        reaction : str
            One of "positive", "negative", "ignored", "neutral".
        signal : str
            What indicated the reaction, e.g. "user_said_thanks",
            "user_skipped_song", "no_response_5min".
        """
        for entry in self.entries:
            if entry["action_id"] == action_id:
                entry["reaction"] = reaction
                entry["reaction_signal"] = signal
                self._save()
                return
        raise ValueError(f"action_id not found: {action_id}")

    # ------------------------------------------------------------------ #
    #  Inference helpers
    # ------------------------------------------------------------------ #

    def infer_music_feedback(self, action_id: str, skipped_within_seconds: int = 30):
        """
        If the user skipped a song within *skipped_within_seconds* of it
        starting, mark the action as negative feedback.  Otherwise mark
        it as positive.
        """
        entry = self._find(action_id)
        action_time = datetime.fromisoformat(entry["timestamp"])
        elapsed = (datetime.now() - action_time).total_seconds()

        if elapsed <= skipped_within_seconds:
            entry["reaction"] = "negative"
            entry["reaction_signal"] = f"user_skipped_song_within_{int(elapsed)}s"
        else:
            entry["reaction"] = "positive"
            entry["reaction_signal"] = "user_listened"
        self._save()

    def infer_suggestion_feedback(self, action_id: str, responded_within_minutes: int = 5):
        """
        If the user did not respond to a proactive suggestion within
        *responded_within_minutes*, mark as ignored.
        """
        entry = self._find(action_id)
        action_time = datetime.fromisoformat(entry["timestamp"])
        elapsed_min = (datetime.now() - action_time).total_seconds() / 60

        if elapsed_min >= responded_within_minutes and entry["reaction"] is None:
            entry["reaction"] = "ignored"
            entry["reaction_signal"] = f"no_response_{responded_within_minutes}min"
            self._save()

    # ------------------------------------------------------------------ #
    #  Query / analytics
    # ------------------------------------------------------------------ #

    def get_preferences(self, action_type: str) -> dict:
        """
        Return reaction counts for a given action_type.

        Example return: {"positive": 5, "negative": 2, "ignored": 3, "neutral": 1, "pending": 0}
        """
        counts: dict[str, int] = {
            "positive": 0,
            "negative": 0,
            "ignored": 0,
            "neutral": 0,
            "pending": 0,
        }
        for entry in self.entries:
            if entry["action_type"] == action_type:
                reaction = entry["reaction"]
                if reaction is None:
                    counts["pending"] += 1
                else:
                    counts[reaction] = counts.get(reaction, 0) + 1
        return counts

    def get_disliked(self, action_type: str, min_negative: int = 2) -> list[str]:
        """
        Return action_details that received negative feedback at least
        *min_negative* times for the given action_type.

        Useful for finding songs the user keeps skipping, suggestions
        they keep ignoring, etc.
        """
        neg_counter: Counter = Counter()
        for entry in self.entries:
            if entry["action_type"] == action_type and entry["reaction"] == "negative":
                neg_counter[entry["action_detail"]] += 1
        return [detail for detail, count in neg_counter.items() if count >= min_negative]

    def get_all(self, limit: int | None = None) -> list[dict]:
        """Return feedback entries, newest first."""
        self._cleanup_old()
        items = sorted(
            self.entries,
            key=lambda entry: entry.get("timestamp", ""),
            reverse=True,
        )
        if limit is not None:
            items = items[:limit]
        return items

    # ------------------------------------------------------------------ #
    #  Prompt formatting (Hebrew)
    # ------------------------------------------------------------------ #

    def format_for_prompt(self) -> str:
        """
        Return a concise Hebrew summary of learned preferences,
        suitable for injecting into a system/context prompt.
        """
        self._cleanup_old()

        lines = ["=== מה שלמדתי מתגובות ==="]

        # --- songs the user keeps skipping ---
        disliked_songs = self.get_disliked("played_song", min_negative=2)
        if disliked_songs:
            quoted = ", ".join(f'"{s}"' for s in disliked_songs)
            lines.append(f"• שירים שהמשתמש דילג עליהם: {quoted} — לא לנגן שוב")

        # --- suggestions that were ignored ---
        ignored_suggestions = self._top_ignored("proactive_suggestion", min_count=2)
        if ignored_suggestions:
            parts = []
            for detail, count in ignored_suggestions:
                parts.append(f"{detail} ({count} פעמים)")
            lines.append(f"• הצעות שהתעלם מהן: {', '.join(parts)} — להפחית תדירות")

        # --- things the user liked ---
        liked = self._top_liked(min_count=2)
        if liked:
            parts = []
            for detail, count in liked:
                parts.append(f"{detail} ({count} תגובות חיוביות)")
            lines.append(f"• דברים שאהב: {', '.join(parts)}")

        if len(lines) == 1:
            return ""  # nothing meaningful to report yet
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  Maintenance
    # ------------------------------------------------------------------ #

    def _cleanup_old(self):
        """Remove entries older than RETENTION_DAYS."""
        cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
        before = len(self.entries)
        self.entries = [
            e for e in self.entries
            if datetime.fromisoformat(e["timestamp"]) >= cutoff
        ]
        if len(self.entries) != before:
            self._save()

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _find(self, action_id: str) -> dict:
        for entry in self.entries:
            if entry["action_id"] == action_id:
                return entry
        raise ValueError(f"action_id not found: {action_id}")

    def _top_ignored(self, action_type: str, min_count: int = 2, limit: int = 5) -> list[tuple[str, int]]:
        """Return (action_detail, count) pairs for ignored actions."""
        counter: Counter = Counter()
        for entry in self.entries:
            if entry["action_type"] == action_type and entry["reaction"] == "ignored":
                counter[entry["action_detail"]] += 1
        return [
            (detail, count)
            for detail, count in counter.most_common(limit)
            if count >= min_count
        ]

    def _top_liked(self, min_count: int = 2, limit: int = 5) -> list[tuple[str, int]]:
        """Return (action_detail, count) pairs for positively-received actions across all types."""
        counter: Counter = Counter()
        for entry in self.entries:
            if entry["reaction"] == "positive":
                counter[entry["action_detail"]] += 1
        return [
            (detail, count)
            for detail, count in counter.most_common(limit)
            if count >= min_count
        ]
