# Jarvis Roadmap

This repository is in an early public stage. The goal is to turn Jarvis from a powerful personal build into a cleaner, reproducible local-first platform.

## Current status

What already exists in the repo:

- Central orchestration layer
- Multi-provider model backend
- Persistent memory
- Real-time dashboard
- Large skill surface
- Voice pipeline
- Messaging channels
- Permission and resilience layers

## Near-term priorities

### 1. Repository polish
- Rewrite the README to match reality: local-first, cloud-optional
- Move screenshots and logs into `docs/assets/` or remove them from root
- Add badges for Python version and CI status
- Add contribution notes and setup troubleshooting

### 2. Reproducible setup
- Add a `Dockerfile` for core runtime
- Add `docker-compose.yml` for optional services
- Split optional dependencies from core dependencies if needed
- Document minimum working local configuration

### 3. CI and quality
- Keep GitHub Actions green on every push
- Add linting and formatting checks
- Add smoke tests for boot + configuration loading
- Mark integration tests clearly

### 4. Product clarity
- Separate personal experiments from stable features
- Define which skills are production-ready vs experimental
- Document required hardware/services per skill
- Add a demo GIF or short video

## Mid-term milestones

### Milestone A — Stable local core
A clean local-only path with Ollama, memory, dashboard, and a minimal useful skill set.

### Milestone B — Reliable voice mode
A dependable wake-word and voice loop that can run for long sessions without drifting or freezing.

### Milestone C — Agentic automation
Safer autonomous flows with permissions, auditability, and rollback-aware actions.

### Milestone D — Smart home + device layer
A polished control surface for Home Assistant, Apple TV, printers, and local machine actions.

## Long-term direction

- A true Jarvis desktop runtime with a high-end UI
- Better proactive behavior and context awareness
- Hardware integration that feels native, not bolted on
- Cleaner plugin/skill SDK for rapid expansion

## Not in scope for now

- Pretending every feature is mature
- Claiming fully local execution when optional cloud features are enabled
- Expanding feature count at the cost of reliability
