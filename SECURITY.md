# Security Policy

## ⚠️ Important Warning

Jarvis can **execute shell commands, control smart-home devices, send messages,
run browser automation, and install new code on your machine**.

Before running Jarvis, review the permission settings in `.env`:

```env
# Require confirmation before CRITICAL actions (3D print, code execution)
# Default: false — change to true in shared or sensitive environments
JARVIS_SAFE_MODE=false

# Log every action Jarvis takes (recommended: always true)
JARVIS_AUDIT_LOG_ENABLED=true
```

Never run Jarvis with elevated privileges (root / sudo).

---

## Scope

This is a personal, local-first project. Security issues that apply:

- **Local privilege escalation** — skill code that escapes its intended scope
- **Secrets leaking** — API keys written to logs, memory files, or the repo
- **Self-improvement abuse** — the `self_improve` skill generating or installing malicious code
- **Prompt injection** — adversarial input from external channels (WhatsApp, Telegram) causing unintended actions

Out of scope: vulnerabilities that require physical access to the machine running Jarvis.

---

## Reporting a Vulnerability

1. **Do not open a public GitHub issue** for security bugs.
2. Email the maintainer directly, or open a [GitHub Security Advisory](https://github.com/liad0706/Jarvis/security/advisories/new) (private disclosure).
3. Include: description, reproduction steps, and potential impact.
4. Expected response: acknowledgement within 7 days.

---

## What NOT to post publicly

- Your `.env` file or any API keys
- Your `USER.md`, `MEMORY.md`, or `SOUL.md` — these contain personal data
- Home Assistant tokens or smart-home credentials
- WhatsApp / Telegram session files (`whatsapp/auth/`)
