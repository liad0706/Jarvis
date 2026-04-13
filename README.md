# Jarvis

A local-first, personal AI assistant inspired by Iron Man's JARVIS. Runs on your machine, speaks Hebrew, controls your smart home, and executes tasks autonomously.

---

## Features

- **Local-first** — LLM inference via [Ollama](https://ollama.ai); conversation data stays on your machine by default
- **Multi-provider LLM** — Ollama (local), OpenAI, Anthropic/Claude, Codex with automatic fallback
- **Hybrid memory** — FAISS vector search + BM25 + SQLite, persisted across sessions
- **33+ skills** — code generation, music, smart home, 3D printing, browser automation, calendar, and more
- **Voice loop** — continuous wake-word listening, STT (Google Speech\*) + TTS (ElevenLabs\*)
- **Real-time dashboard** — web UI with WebSocket streaming at `localhost:8550`
- **Multi-channel** — WhatsApp, Telegram, Discord, CLI
- **Proactive engine** — autonomously suggests and executes tasks based on time and context
- **Self-improvement** — generates and installs new skills on its own
- **Security** — 6 layers: permission gates, sandbox, audit log, circuit breaker, rate limiter, policy engine

> \* Voice mode uses cloud services: Google Speech Recognition (STT) and ElevenLabs (TTS).
> Set `JARVIS_VOICE_ENABLED=false` to keep everything fully local.

---

## Requirements

- Python 3.11+
- [Ollama](https://ollama.ai) for local inference
- Node.js 18+ (WhatsApp bridge only)

---

## Setup

```bash
# 1. Clone
git clone https://github.com/liad0706/Jarvis.git
cd Jarvis

# 2. Run the one-shot setup script (installs everything from scratch)
bash setup.sh

# 3. Configure
nano .env   # fill in your API keys

# 4. Run
source .venv/bin/activate
python main.py
```

> `setup.sh` installs Python 3.11+, venv, all pip packages, Playwright browsers,
> Ollama, the recommended embedding model, and the Node.js WhatsApp bridge.
> Works on Ubuntu/Debian and macOS. See `bash setup.sh --help` for flags.

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

## Minimum Working Path

Just want to run a local AI assistant with chat + memory + dashboard?

```bash
git clone https://github.com/liad0706/Jarvis.git && cd Jarvis
bash setup.sh --skip-whatsapp
```

In `.env`, set only:
```env
JARVIS_LLM_PROVIDER=ollama
JARVIS_OLLAMA_MODEL=qwen2.5:7b
JARVIS_DASHBOARD_ENABLED=true
JARVIS_VOICE_ENABLED=false
JARVIS_WHATSAPP_ENABLED=false
```

Then `python main.py` → open `http://localhost:8550`. Done.
Everything else (voice, smart home, WhatsApp, Telegram, 3D printing) is optional.

---

## Skills

### Stable

| Skill | What it does |
|-------|-------------|
| `system_control` | Shell commands, screenshots, clipboard, volume |
| `spotify_controller` | Play, pause, search, queue music |
| `smart_home` | Control lights and devices via Home Assistant |
| `code_writer` | Generate, explain, and run code |
| `file_manager` | Read, write, search local files |
| `web_research` | Web search and page scraping |
| `weather_skill` | Current weather and forecast |
| `timer_skill` | Set timers and reminders |
| `scheduler_skill` | Schedule tasks at specific times |
| `memory_skill` | Query and manage long-term memory |
| `document_rag` | Q&A over local documents |
| `calendar_skill` | Read and create calendar events |

### Experimental

| Skill | Status | Notes |
|-------|--------|-------|
| `apple_tv` | Experimental | Requires pyatv pairing |
| `creality_print` / `creality_api_skill` | Experimental | Creality K1 Max only |
| `browser_agent` | Experimental | Playwright-based, may break on site changes |
| `appointment_booker` | Experimental | Hardcoded to specific booking site |
| `self_improve` | Experimental | Generates + installs new skills at runtime |
| `ruview_sensor` | Experimental | WiFi-based human presence (CSI hardware required) |
| `model_downloader` | Experimental | Auto-downloads 3D models from Printables |
| `iphone_skill` | Experimental | iOS shortcuts integration |
| `screen_reader` | Experimental | Vision-based screen understanding |

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
