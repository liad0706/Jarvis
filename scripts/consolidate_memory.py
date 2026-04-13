"""Memory consolidation script (Dream) — adapted from Claude Code's consolidationPrompt.ts.

Run periodically (daily cron or manually) to synthesize recent episodic memories,
session summaries, and conversation history into durable, well-organized memory files.

Usage:
    python scripts/consolidate_memory.py [--dry-run]

Phases (from Claude Code's Dream pattern):
    1. Orient  — read current memory index and topic files
    2. Gather  — collect recent signal from episodic DB + conversation state
    3. Consolidate — ask LLM to merge new signal into existing topic files
    4. Prune   — update MEMORY.md index, remove stale entries
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Allow running from repo root or scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import get_settings
from config.settings import ollama_runtime_options

logger = logging.getLogger(__name__)

MEMORY_DIR = PROJECT_ROOT / "memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
DATA_DIR = PROJECT_ROOT / "data"
EPISODIC_DB = DATA_DIR / "memory.db"
CONVERSATION_STATE = DATA_DIR / "conversation_state.json"

MAX_INDEX_LINES = 200


# ── Phase 1: Orient ──────────────────────────────────────────────────────────

def orient() -> dict:
    """Read the current memory state."""
    state: dict = {"topic_files": {}, "index_content": "", "index_lines": 0}

    if MEMORY_INDEX.exists():
        content = MEMORY_INDEX.read_text(encoding="utf-8")
        state["index_content"] = content
        state["index_lines"] = len(content.splitlines())

    for md_file in sorted(MEMORY_DIR.glob("*.md")):
        if md_file.name == "MEMORY.md":
            continue
        state["topic_files"][md_file.name] = md_file.read_text(encoding="utf-8")

    logger.info(
        "Orient: %d topic files, index has %d lines",
        len(state["topic_files"]),
        state["index_lines"],
    )
    return state


# ── Phase 2: Gather recent signal ────────────────────────────────────────────

async def gather_recent_signal(hours: int = 24) -> dict:
    """Collect recent episodic memories and conversation snippets."""
    signal: dict = {"episodic": [], "conversations": [], "gathered_at": datetime.now().isoformat()}
    cutoff = time.time() - (hours * 3600)

    # Episodic memories from SQLite
    if EPISODIC_DB.exists():
        import aiosqlite
        async with aiosqlite.connect(str(EPISODIC_DB)) as db:
            cursor = await db.execute(
                "SELECT id, type, content, metadata, created_at FROM episodic_memory "
                "WHERE created_at > ? ORDER BY created_at DESC LIMIT 50",
                (cutoff,),
            )
            rows = await cursor.fetchall()
            for row in rows:
                signal["episodic"].append({
                    "id": row[0],
                    "type": row[1],
                    "content": row[2],
                    "metadata": row[3],
                    "created_at": row[4],
                })

    # Recent conversation state
    if CONVERSATION_STATE.exists():
        try:
            convos = json.loads(CONVERSATION_STATE.read_text(encoding="utf-8"))
            if isinstance(convos, list):
                signal["conversations"] = convos[-20:]  # last 20 messages
        except (json.JSONDecodeError, OSError):
            pass

    logger.info(
        "Gather: %d episodic memories, %d conversation messages",
        len(signal["episodic"]),
        len(signal["conversations"]),
    )
    return signal


# ── Phase 3: Consolidate ─────────────────────────────────────────────────────

def _build_consolidation_prompt(orient_state: dict, signal: dict) -> str:
    """Build the LLM prompt for consolidation."""
    existing_files = ""
    for fname, content in orient_state["topic_files"].items():
        existing_files += f"\n### {fname}\n{content[:500]}\n"

    episodic_text = ""
    for ep in signal["episodic"][:30]:
        ts = datetime.fromtimestamp(ep["created_at"]).strftime("%Y-%m-%d %H:%M")
        episodic_text += f"- [{ts}] ({ep['type']}) {ep['content'][:200]}\n"

    convo_text = ""
    for msg in signal["conversations"][-15:]:
        role = msg.get("role", "?")
        content = msg.get("content", "")[:150]
        convo_text += f"- {role}: {content}\n"

    return f"""# Memory Consolidation Task

You are performing a memory consolidation pass. Synthesize recent information into
durable, well-organized memory topic files.

## Current Memory Index
{orient_state["index_content"][:2000] or "(empty)"}

## Existing Topic Files
{existing_files or "(none yet)"}

## Recent Episodic Memories (new signal)
{episodic_text or "(none)"}

## Recent Conversation Snippets
{convo_text or "(none)"}

---

## Instructions

1. **Merge** new signal into existing topic files rather than creating duplicates.
2. **Convert** relative dates to absolute dates (e.g., "yesterday" → "{(datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')}").
3. **Delete** contradicted facts — if new info disproves an old memory, flag it.
4. **Keep** the index under {MAX_INDEX_LINES} lines. Each entry: one line, under ~150 chars.

Reply with ONLY valid JSON (no markdown fences):
{{
  "updates": [
    {{"file": "topic_name.md", "action": "update|create|delete", "content": "new full content for the file"}},
    ...
  ],
  "index": "full updated MEMORY.md content",
  "summary": "brief description of what changed"
}}

If nothing needs changing, return: {{"updates": [], "index": "<current index unchanged>", "summary": "No changes needed."}}
"""


async def consolidate(orient_state: dict, signal: dict, dry_run: bool = False) -> dict:
    """Ask the LLM to consolidate memories."""
    import ollama as ollama_lib

    settings = get_settings()
    prompt = _build_consolidation_prompt(orient_state, signal)

    client = ollama_lib.AsyncClient(host=settings.ollama_host)
    kw: dict = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": "You are a memory consolidation agent. Output ONLY valid JSON."},
            {"role": "user", "content": prompt},
        ],
    }
    oopts = ollama_runtime_options(settings)
    if oopts:
        kw["options"] = oopts

    response = await client.chat(**kw)
    raw = response.message.content.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("LLM returned invalid JSON:\n%s", raw[:500])
        return {"error": "Invalid JSON from LLM", "raw": raw[:500]}

    if dry_run:
        logger.info("[DRY-RUN] Would apply: %s", result.get("summary", ""))
        return result

    # Apply updates
    MEMORY_DIR.mkdir(exist_ok=True)
    for update in result.get("updates", []):
        fname = update.get("file", "")
        action = update.get("action", "")
        content = update.get("content", "")

        if not fname or not fname.endswith(".md"):
            continue

        fpath = MEMORY_DIR / fname
        if action == "delete":
            if fpath.exists():
                fpath.unlink()
                logger.info("Deleted: %s", fname)
        elif action in ("create", "update"):
            fpath.write_text(content, encoding="utf-8")
            logger.info("%s: %s (%d chars)", action.capitalize(), fname, len(content))

    # Update index
    new_index = result.get("index", "")
    if new_index and new_index != "<current index unchanged>":
        lines = new_index.splitlines()[:MAX_INDEX_LINES]
        MEMORY_INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("Updated MEMORY.md index (%d lines)", len(lines))

    return result


# ── Phase 4: Prune (handled by consolidate LLM output) ───────────────────────

# ── Main ──────────────────────────────────────────────────────────────────────

async def main(dry_run: bool = False, hours: int = 24):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    logger.info("=== Memory Consolidation Start (hours=%d, dry_run=%s) ===", hours, dry_run)

    # Phase 1
    state = orient()

    # Phase 2
    signal = await gather_recent_signal(hours=hours)

    if not signal["episodic"] and not signal["conversations"]:
        logger.info("No recent signal to consolidate. Done.")
        return

    # Phase 3 + 4
    result = await consolidate(state, signal, dry_run=dry_run)

    summary = result.get("summary", result.get("error", "unknown"))
    logger.info("=== Consolidation Complete: %s ===", summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Jarvis memory consolidation (Dream)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing files")
    parser.add_argument("--hours", type=int, default=24, help="Look back N hours for new signal (default: 24)")
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run, hours=args.hours))
