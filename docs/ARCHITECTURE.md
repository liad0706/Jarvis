# Jarvis Architecture

Jarvis is a local-first assistant with optional cloud providers, built around a central orchestrator and a growing set of skills.

## Design goals

- Local-first runtime with Ollama as the default brain
- Optional cloud providers for fallback or higher-capability tasks
- Clear permission boundaries before risky actions
- Persistent memory across sessions
- Multi-channel access: CLI, dashboard, Telegram, Discord, WhatsApp
- Extensible skill system instead of hard-coded one-off features

## High-level flow

```text
User / Voice / Channel
        |
        v
   Orchestrator
        |
        +--> Context Builder
        |        |
        |        +--> Memory (SQLite + FAISS/BM25)
        |
        +--> Provider Layer
        |        |
        |        +--> Ollama (default)
        |        +--> OpenAI / Anthropic / Codex (optional)
        |
        +--> Tool Executor
                 |
                 +--> Skills
                        |
                        +--> Smart Home
                        +--> Apple TV
                        +--> Browser Agent
                        +--> File Manager
                        +--> Code Writer
                        +--> Creality Print
                        +--> Scheduler
                        +--> Dynamic Skills
```

## Core modules

### `main.py`
Application entry point. Boots settings, initializes subsystems, and starts the runtime.

### `config/settings.py`
Loads environment variables and central runtime configuration.

### `core/orchestrator.py`
The main coordination layer. Receives requests, builds context, selects a provider, invokes tools, and returns the final response.

### `core/context_builder.py`
Builds the model-facing context by combining system instructions, short-term conversation state, and relevant memory retrieval.

### `core/providers.py`
Abstracts the model backend. Keeps provider-specific logic away from the orchestration layer.

### `core/tool_executor.py`
Executes tool calls emitted by the LLM and routes them to the registered skills.

### `core/memory.py`
Stores conversations, facts, and episodic memory in SQLite.

### `core/faiss_memory.py`
Adds semantic retrieval through FAISS and keyword support through BM25 for better recall.

### `core/permissions.py`
Defines risk levels and permission gates for actions such as file writes, external calls, or critical operations.

### `core/resilience.py`
Handles retries, rate limiting, and circuit-breaker logic to keep the assistant from spiraling during failures.

### `core/proactive_engine.py`
Handles proactive suggestions and autonomous task execution based on time, state, and available context.

## Skills layer

The `skills/` directory is the action surface of Jarvis. Each skill owns a bounded domain. This makes the system easier to reason about, test, and expand.

Examples:

- `smart_home.py` for Home Assistant and device control
- `apple_tv.py` for remote interactions
- `browser_agent.py` for Playwright-driven browser workflows
- `creality_print.py` for 3D printer operations
- `system_control.py` for screenshots, shell commands, clipboard, and local automation
- `dynamic/` for auto-generated skills

## Voice and channels

### `voice/`
Voice input/output pipeline, including speech-to-text, text-to-speech, and the continuous voice loop.

### `channels/`
Messaging and bot integrations such as Telegram, Discord, and WhatsApp.

### `dashboard/`
FastAPI + WebSocket user interface for real-time interaction and status display.

## Operating modes

Jarvis should be described as **local-first**, not purely local.

Reason:

- Core runtime can operate locally with Ollama
- Memory and orchestration are local
- Some optional features depend on external providers or APIs
- Voice and model fallback can be cloud-backed depending on configuration

That distinction matters for trust. "Local-first, cloud-optional" is accurate. "100% local" is only accurate for specific configurations.

## Recommended next improvements

1. Add a clean boot diagram to the README
2. Separate optional cloud features from the default local path
3. Add CI checks for syntax and imports
4. Add screenshots/GIFs under `docs/assets/`
5. Publish a sharper roadmap with near-term milestones
