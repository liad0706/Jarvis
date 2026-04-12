# Jarvis

A fully local, personal AI assistant inspired by Iron Man's JARVIS. Runs on your machine, speaks Hebrew, controls your smart home, and executes tasks autonomously — no cloud required.

---

## Features

- **100% Local** — powered by [Ollama](https://ollama.ai), no data leaves your machine
- **Multi-provider LLM** — Ollama, OpenAI, Anthropic/Claude, Codex with automatic fallback
- **Hybrid memory** — FAISS vector search + BM25 + SQLite, persisted across sessions
- **33+ skills** — code generation, music, smart home, 3D printing, browser automation, calendar, and more
- **Voice loop** — continuous wake-word listening, STT (Google Speech) + TTS (ElevenLabs)
- **Real-time dashboard** — web UI with WebSocket streaming at `localhost:8550`
- **Multi-channel** — WhatsApp, Telegram, Discord, CLI
- **Proactive engine** — autonomously suggests and executes tasks based on time and context
- **Self-improvement** — generates and installs new skills on its own
- **Security** — 6 layers: permission gates, sandbox, audit log, circuit breaker, rate limiter, policy engine

---

## Requirements

- Python 3.11+
- [Ollama](https://ollama.ai) for local inference
- Node.js 18+ (WhatsApp bridge only)

---

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/liad0706/Jarvis.git
cd Jarvis

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with your settings

# 4. Pull required Ollama models
ollama pull qwen2.5:7b
ollama pull nomic-embed-text

# 5. Run
python main.py
```

---

## Public Repo Notes

This repository is set up so runtime and personal files stay local instead of being pushed to GitHub.

- Keep `.env`, `USER.md`, `SOUL.md`, `MEMORY.md`, `memory/`, `data/`, and `scripts/vacuum_map.png` local-only.
- Public templates are included as `USER.example.md`, `SOUL.example.md`, and `MEMORY.example.md`.
- On a fresh clone you can copy the example files, or let Jarvis create the local files during onboarding and soul setup.

---

## Configuration

Copy `.env.example` to `.env` and fill in the values you need:

```env
# LLM provider: ollama | openai | codex | claude
JARVIS_LLM_PROVIDER=ollama
JARVIS_OLLAMA_MODEL=qwen2.5:7b

# Optional cloud providers
JARVIS_OPENAI_API_KEY=
JARVIS_ANTHROPIC_API_KEY=

# Voice (ElevenLabs TTS + Google STT)
JARVIS_VOICE_ENABLED=false
JARVIS_ELEVENLABS_API_KEY=

# Dashboard
JARVIS_DASHBOARD_ENABLED=true
JARVIS_DASHBOARD_PORT=8550

# Smart home
JARVIS_HA_URL=http://localhost:8123
JARVIS_HA_TOKEN=

# Messaging
JARVIS_TELEGRAM_TOKEN=
JARVIS_WHATSAPP_ENABLED=false
```

See `.env.example` for the full list.

---

## Project Structure

```
Jarvis/
├── main.py                   # Entry point
├── config/settings.py        # Pydantic settings from .env
├── core/
│   ├── orchestrator.py       # Main request-response loop
│   ├── context_builder.py    # Builds system prompt + memory context
│   ├── tool_executor.py      # Executes LLM tool calls
│   ├── providers.py          # Ollama / OpenAI / Anthropic abstraction
│   ├── memory.py             # SQLite — conversations, facts, episodic memory
│   ├── faiss_memory.py       # FAISS + BM25 hybrid search (disk-cached)
│   ├── skill_base.py         # BaseSkill + SkillRegistry
│   ├── proactive_engine.py   # Autonomous task suggestion & execution
│   ├── permissions.py        # Risk gates (READ / WRITE / EXTERNAL / CRITICAL)
│   └── resilience.py         # Circuit breaker, rate limiter, retry
├── skills/                   # 33+ skill modules
│   ├── spotify_controller.py
│   ├── smart_home.py         # Home Assistant integration
│   ├── apple_tv.py
│   ├── creality_print.py     # 3D printer control
│   ├── system_control.py     # Shell, screenshots, clipboard
│   ├── browser_agent.py      # Playwright browser automation
│   └── dynamic/              # Auto-generated skills
├── voice/
│   ├── stt.py                # Speech-to-text
│   ├── tts.py                # Text-to-speech (ElevenLabs)
│   └── voice_loop.py         # Continuous listen → respond loop
├── dashboard/                # FastAPI + WebSocket UI
├── channels/                 # Telegram, Discord, WhatsApp
└── tests/                    # 37+ test files
```

---

## Skills

| Skill | Description |
|-------|-------------|
| `system_control` | Shell commands, screenshots, clipboard, volume |
| `spotify_controller` | Play, pause, search music |
| `smart_home` | Control lights and devices via Home Assistant |
| `apple_tv` | Remote control for Apple TV |
| `creality_print` | Start, pause, monitor 3D prints |
| `browser_agent` | Browser automation with Playwright |
| `code_writer` | Generate code in any language |
| `file_manager` | Read, write, search files |
| `web_research` | Web search and scraping |
| `document_rag` | Q&A over local documents |
| `scheduler_skill` | Schedule tasks at specific times |
| `self_improve` | Auto-generate and install new skills |

---

## Dashboard

Once running, open `http://localhost:8550` in your browser.

---

## WhatsApp Bridge

```bash
cd whatsapp
npm install
node bridge.mjs
```

Run `whatsapp_server.py` separately alongside `main.py`.

---

## Running Tests

```bash
pytest tests/ -x -q
```

---

## License

Personal project — for personal use only.
