"""Conversation branching — fork conversations to try different approaches."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BRANCHES_DIR = Path(__file__).resolve().parent.parent / "data" / "branches"


class ConversationBranch:
    """A snapshot of a conversation at a specific point."""

    def __init__(self, branch_id: str, parent_id: str = "main",
                 messages: list[dict] | None = None,
                 fork_index: int = 0, label: str = ""):
        self.branch_id = branch_id
        self.parent_id = parent_id
        self.messages = messages or []
        self.fork_index = fork_index
        self.label = label
        self.created_at = time.time()

    def to_dict(self) -> dict:
        return {
            "branch_id": self.branch_id,
            "parent_id": self.parent_id,
            "messages": self.messages,
            "fork_index": self.fork_index,
            "label": self.label,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ConversationBranch:
        b = cls(
            branch_id=d["branch_id"],
            parent_id=d.get("parent_id", "main"),
            messages=d.get("messages", []),
            fork_index=d.get("fork_index", 0),
            label=d.get("label", ""),
        )
        b.created_at = d.get("created_at", time.time())
        return b


class BranchManager:
    """Manages conversation branches — fork, switch, list, delete."""

    def __init__(self):
        BRANCHES_DIR.mkdir(parents=True, exist_ok=True)
        self._current_branch = "main"

    def fork(self, conversation: list[dict], at_index: int | None = None,
             label: str = "") -> ConversationBranch:
        """Fork the conversation at a specific message index.

        If at_index is None, fork at the current end.
        Returns the new branch.
        """
        import secrets
        branch_id = f"branch_{secrets.token_hex(4)}"

        if at_index is None:
            at_index = len(conversation)

        # Take messages up to the fork point
        forked_messages = conversation[:at_index]

        branch = ConversationBranch(
            branch_id=branch_id,
            parent_id=self._current_branch,
            messages=forked_messages,
            fork_index=at_index,
            label=label or f"Fork at message {at_index}",
        )

        self._save_branch(branch)
        logger.info("Created branch %s from %s at index %d", branch_id, self._current_branch, at_index)
        return branch

    def switch(self, branch_id: str) -> list[dict] | None:
        """Switch to a branch. Returns the branch's messages, or None if not found."""
        branch = self._load_branch(branch_id)
        if branch:
            self._current_branch = branch_id
            return branch.messages
        return None

    def list_branches(self) -> list[dict]:
        """List all branches."""
        branches = []
        for f in BRANCHES_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                branches.append({
                    "branch_id": data["branch_id"],
                    "parent_id": data.get("parent_id", "main"),
                    "label": data.get("label", ""),
                    "message_count": len(data.get("messages", [])),
                    "created_at": data.get("created_at", 0),
                })
            except Exception:
                pass
        return sorted(branches, key=lambda b: b["created_at"], reverse=True)

    def delete_branch(self, branch_id: str) -> bool:
        """Delete a branch."""
        path = BRANCHES_DIR / f"{branch_id}.json"
        if path.exists():
            path.unlink()
            logger.info("Deleted branch %s", branch_id)
            return True
        return False

    @property
    def current_branch(self) -> str:
        return self._current_branch

    def _save_branch(self, branch: ConversationBranch):
        path = BRANCHES_DIR / f"{branch.branch_id}.json"
        path.write_text(
            json.dumps(branch.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_branch(self, branch_id: str) -> ConversationBranch | None:
        path = BRANCHES_DIR / f"{branch_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ConversationBranch.from_dict(data)
        except Exception as e:
            logger.warning("Failed to load branch %s: %s", branch_id, e)
            return None
