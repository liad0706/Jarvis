from core.skill_base import BaseSkill
import json
import os

class SmartMemorySkill(BaseSkill):
    name = "smart_memory"
    description = "Persistent memory for storing and retrieving structured context."
    REQUIREMENTS: list[str] = []

    def __init__(self):
        self._load_memory()

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        return await method(**(params or {}))

    async def do_store(self, data: dict) -> dict:
        """Store structured context."""
        self.memory.update(data)
        self._save_memory()
        return {"status": "ok"}

    async def do_retrieve(self, key: str) -> dict:
        """Retrieve stored context by key."""
        value = self.memory.get(key, None)
        if value is not None:
            return {"status": "ok", key: value}
        else:
            return {"error": f"Key {key} not found"}

    async def do_summary(self) -> dict:
        """Summarize recent changes in memory."""
        summary = {}
        for k, v in self.memory.items():
            if isinstance(v, (list, dict)):
                last_val = next(iter(v), "N/A")
                summary[k] = {"last_value": last_val}
        return {"status": "ok", "summary": summary}

    def _load_memory(self):
        try:
            with open("memory.json", "r") as f:
                self.memory = json.load(f)
        except FileNotFoundError:
            self.memory = {}

    def _save_memory(self):
        with open("memory.json", "w") as f:
            json.dump(self.memory, f, indent=4)
