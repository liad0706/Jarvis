# MEMORY.md — Long-Term Memory

Last updated: 2026-03-26

## Key Decisions

- [2026-03-26] Jarvis production hardening: implemented 6 new layers — Guardrails/Sandbox, Planner/StateMachine, Advanced Memory, Ops/Resilience, Permissions/Policy, Product UX. All 12 new core modules + 8 test files. 191 tests pass.
- [2025-07-19] Unreal Engine JARVIS: decided C++ over Blueprint for the Unreal side.
- [2025-07-30] Unreal-Python integration: HTTP approach — Unreal sends text, gets JSON actions back.
- Jarvis philosophy: local-first, Ollama-based, no cloud dependency. Free and self-hosted.

## Architecture Notes

Jarvis is a Python async CLI assistant:
- **LLM:** Ollama (qwen3:8b local)
- **Memory:** SQLite (conversations, facts, decisions, summaries, embeddings)
- **Skills:** BaseSkill plugin system with static + dynamic (self-generated) skills
- **Security:** Policy engine (import/package allowlists), subprocess sandbox for dynamic skills
- **Planning:** LLM-based task decomposition, state machine with checkpoints
- **Resilience:** Circuit breaker, rate limiter, timeouts on all external calls
- **Permissions:** Risk-level gate (READ/WRITE/EXTERNAL/CRITICAL), audit log, safe-mode/dry-run
- **UX:** EventBus-driven progress reporting, consistent personality

## User's Working Style

- Learns by doing, not reading. Always provide runnable examples.
- Thinks in full systems — pull him back to MVP.
- Gets excited fast, drops fast if stuck. Break everything into small steps.
- Hates silence during long tasks. Always update progress.
- Prefers Hebrew, mixes with English naturally.
- Wants blunt truth, not encouragement.

## Hardware Available

Full powerful PC (Ryzen 7600X, GPU XT, 32GB DDR5, 2TB NVMe), Raspberry Pi 5, Arduino Uno, 15 servo motors, Creality K1 Max 3D printer, Yeelight smart lamp, Quest 3, iPhone. Not limited by performance.

## Lessons Learned

- [2026-03-26] First session. Initialized all memory files and production-hardened Jarvis with 6 layers.
