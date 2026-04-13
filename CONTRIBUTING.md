# Contributing to Jarvis

## Getting Started

```bash
git clone https://github.com/liad0706/Jarvis.git
cd Jarvis
bash setup.sh          # installs everything
cp .env.example .env   # configure your keys
source .venv/bin/activate
python main.py
```

---

## Running Tests

```bash
# Fast unit tests — no external services needed
pytest tests/ -q -m "not live and not integration"

# Integration tests — require Ollama running locally
pytest tests/ -q -m "integration and not live"

# All tests (may fail without real devices)
pytest tests/ -q
```

**Test markers:**
- `@pytest.mark.live` — requires real hardware (smart home, Apple TV, 3D printer)
- `@pytest.mark.integration` — requires Ollama or other local services
- No marker → pure unit test, always runs in CI

---

## Adding a Skill

1. Create `skills/your_skill.py`:

```python
from core.skill_base import BaseSkill

class YourSkill(BaseSkill):
    name = "your_skill"
    description = "One sentence describing what this skill does."

    def get_tools(self) -> list[dict]:
        return [
            {
                "name": "your_action",
                "description": "What this action does.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "param": {"type": "string", "description": "..."}
                    },
                    "required": ["param"],
                },
            }
        ]

    async def execute(self, action: str, params: dict | None = None) -> dict:
        if action == "your_action":
            return await self._your_action(**(params or {}))
        return {"error": f"Unknown action: {action}"}

    async def _your_action(self, param: str) -> dict:
        # your logic here
        return {"result": param}
```

2. Register it in `core/bootstrap.py` (or it will be auto-discovered if in `skills/`).

3. If the skill contacts external services or modifies files, add a risk level in `core/permissions.py` → `DEFAULT_RISK_OVERRIDES`.

4. Write at least one unit test in `tests/test_your_skill.py`.

---

## Code Style

- Python 3.11+, async/await throughout
- No type annotations required, but add them if they make intent clearer
- No docstrings required on internal methods; add them on public APIs
- Keep skills focused — one `skills/*.py` per domain
- Do not commit `.env`, `USER.md`, `MEMORY.md`, `SOUL.md`, or `memory/` files

---

## Commit Messages

Use the imperative mood, short subject line (≤ 72 chars):

```
Add weather skill with OpenWeatherMap support
Fix permission gate not blocking CRITICAL actions
Refactor orchestrator: split routing from execution
```

No issue tracker references required for personal contributions.

---

## Pull Requests

- Branch off `master`
- One logical change per PR
- CI must pass (unit tests + smoke test)
- Describe what changed and why — not just what the diff shows
