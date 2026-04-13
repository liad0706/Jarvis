#!/usr/bin/env bash
# =============================================================================
#  Jarvis — One-shot setup script
#  Usage:  bash setup.sh
#          bash setup.sh --skip-ollama   (skip Ollama install / model pull)
#          bash setup.sh --skip-whatsapp (skip Node.js / WhatsApp bridge)
# =============================================================================
set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}[OK]${RESET}  $*"; }
info() { echo -e "${CYAN}[..]${RESET}  $*"; }
warn() { echo -e "${YELLOW}[!!]${RESET}  $*"; }
err()  { echo -e "${RED}[ERR]${RESET} $*" >&2; }
step() { echo -e "\n${BOLD}${CYAN}══ $* ══${RESET}"; }

SKIP_OLLAMA=false
SKIP_WHATSAPP=false
for arg in "$@"; do
  [[ "$arg" == "--skip-ollama"   ]] && SKIP_OLLAMA=true
  [[ "$arg" == "--skip-whatsapp" ]] && SKIP_WHATSAPP=true
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║       Jarvis — Setup Script          ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${RESET}"

# ── detect OS ────────────────────────────────────────────────────────────────
OS="unknown"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
  OS="linux"
elif [[ "$OSTYPE" == "darwin"* ]]; then
  OS="macos"
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OS" == "Windows_NT" ]]; then
  OS="windows"
fi
info "Detected OS: $OS"

# ── helper: check command exists ─────────────────────────────────────────────
need() { command -v "$1" &>/dev/null; }

# =============================================================================
# STEP 1 — System packages (apt / brew)
# =============================================================================
step "System dependencies"

if [[ "$OS" == "linux" ]]; then
  if need apt-get; then
    info "Installing system packages via apt..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
      python3 python3-pip python3-venv python3-dev \
      portaudio19-dev \
      libsndfile1 \
      ffmpeg \
      curl wget git \
      build-essential \
      libssl-dev libffi-dev \
      2>/dev/null || warn "Some apt packages may have failed — continuing"
    ok "apt packages done"
  else
    warn "apt-get not found; skipping system package install"
  fi

elif [[ "$OS" == "macos" ]]; then
  if need brew; then
    info "Installing system packages via Homebrew..."
    brew install portaudio libsndfile ffmpeg 2>/dev/null || true
    ok "brew packages done"
  else
    warn "Homebrew not found. Install from https://brew.sh then re-run."
  fi

elif [[ "$OS" == "windows" ]]; then
  warn "Windows detected — install PortAudio / ffmpeg manually if audio fails."
fi

# =============================================================================
# STEP 2 — Python 3.11+
# =============================================================================
step "Python 3.11+"

PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3 python; do
  if need "$cmd"; then
    VER=$("$cmd" -c "import sys; print(sys.version_info[:2])" 2>/dev/null || echo "(0, 0)")
    MAJOR=$(echo "$VER" | tr -d '()' | cut -d',' -f1 | tr -d ' ')
    MINOR=$(echo "$VER" | tr -d '()' | cut -d',' -f2 | tr -d ' ')
    if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 11 ]]; then
      PYTHON="$cmd"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  err "Python 3.11+ not found!"
  if [[ "$OS" == "linux" ]]; then
    info "Trying to install python3.11 via apt..."
    sudo apt-get install -y python3.11 python3.11-venv python3.11-dev || {
      err "Failed. Install manually: https://www.python.org/downloads/"
      exit 1
    }
    PYTHON="python3.11"
  else
    err "Install Python 3.11+ from https://www.python.org/downloads/ then re-run."
    exit 1
  fi
fi

PY_VER=$("$PYTHON" --version)
ok "$PY_VER found at: $(which "$PYTHON")"

# =============================================================================
# STEP 3 — Virtual environment
# =============================================================================
step "Virtual environment"

VENV_DIR="$SCRIPT_DIR/.venv"

if [[ -d "$VENV_DIR" ]]; then
  ok "venv already exists at .venv — reusing"
else
  info "Creating virtual environment..."
  "$PYTHON" -m venv "$VENV_DIR"
  ok "venv created"
fi

# Activate
source "$VENV_DIR/bin/activate"
PIP="$VENV_DIR/bin/pip"
PYTHON_VENV="$VENV_DIR/bin/python"

info "Upgrading pip, setuptools, wheel..."
"$PIP" install --quiet --upgrade pip setuptools wheel

# =============================================================================
# STEP 4 — Python dependencies
# =============================================================================
step "Python packages"

# Build requirements list — exclude Windows-only packages on non-Windows
REQS_FILE="$SCRIPT_DIR/requirements.txt"
TMP_REQS="$(mktemp /tmp/jarvis_reqs_XXXXXX.txt)"

if [[ "$OS" != "windows" ]]; then
  info "Filtering out Windows-only packages (pywin32, pywinauto, pyautogui)..."
  grep -vE "^\s*(pywin32|pywinauto|pyautogui)" "$REQS_FILE" > "$TMP_REQS"
else
  cp "$REQS_FILE" "$TMP_REQS"
fi

info "Installing Python packages (this may take a few minutes)..."
"$PIP" install --quiet -r "$TMP_REQS" || {
  warn "Some packages failed — trying one by one..."
  while IFS= read -r line; do
    # skip comments and blank lines
    [[ "$line" =~ ^#.*$ || -z "${line// }" ]] && continue
    "$PIP" install --quiet "$line" 2>/dev/null \
      && ok "  $line" \
      || warn "  SKIP: $line"
  done < "$TMP_REQS"
}
rm -f "$TMP_REQS"

ok "Python packages installed"

# =============================================================================
# STEP 5 — Playwright browsers
# =============================================================================
step "Playwright browsers"

if "$VENV_DIR/bin/python" -c "import playwright" 2>/dev/null; then
  info "Installing Playwright browser binaries (chromium)..."
  "$VENV_DIR/bin/playwright" install chromium 2>/dev/null \
    && ok "Playwright chromium ready" \
    || warn "Playwright browser install failed — browser skills won't work"
else
  warn "Playwright not installed — skipping browser install"
fi

# =============================================================================
# STEP 6 — Ollama
# =============================================================================
if [[ "$SKIP_OLLAMA" == false ]]; then
  step "Ollama (local LLM)"

  if need ollama; then
    ok "Ollama already installed: $(ollama --version 2>/dev/null || echo 'version unknown')"
  else
    info "Downloading and installing Ollama..."
    if [[ "$OS" == "linux" || "$OS" == "macos" ]]; then
      curl -fsSL https://ollama.com/install.sh | sh \
        && ok "Ollama installed" \
        || { warn "Ollama install failed — install manually from https://ollama.com"; }
    else
      warn "Windows: download Ollama from https://ollama.com/download"
    fi
  fi

  # Pull recommended embedding model (always needed for memory)
  if need ollama; then
    # Start ollama serve in background if not running
    if ! curl -sf http://localhost:11434/api/tags &>/dev/null; then
      info "Starting Ollama server..."
      ollama serve &>/dev/null &
      OLLAMA_PID=$!
      sleep 3
      info "Ollama server started (PID $OLLAMA_PID)"
    fi

    info "Pulling embedding model: nomic-embed-text..."
    ollama pull nomic-embed-text \
      && ok "nomic-embed-text ready" \
      || warn "Failed to pull nomic-embed-text — memory features may not work"

    # Pull chat model only if user hasn't set one yet
    if [[ -f "$SCRIPT_DIR/.env" ]]; then
      CHAT_MODEL=$(grep -E "^JARVIS_OLLAMA_MODEL=" "$SCRIPT_DIR/.env" | cut -d'=' -f2 | tr -d '"' | tr -d "'")
    fi
    CHAT_MODEL="${CHAT_MODEL:-}"

    if [[ -z "$CHAT_MODEL" ]]; then
      echo ""
      warn "No JARVIS_OLLAMA_MODEL set. Recommended models:"
      echo "    qwen2.5:7b   — fast, good Hebrew  (recommended)"
      echo "    llama3.2:3b  — tiny & fast, weaker Hebrew"
      echo "    qwen3:8b     — slower, more 'thinking'"
      read -rp "  Pull a model now? Enter name or press ENTER to skip: " PULL_MODEL
      if [[ -n "$PULL_MODEL" ]]; then
        info "Pulling $PULL_MODEL..."
        ollama pull "$PULL_MODEL" \
          && ok "$PULL_MODEL ready" \
          || warn "Failed to pull $PULL_MODEL"
      fi
    else
      info "Chat model already set: $CHAT_MODEL — skipping pull"
    fi
  fi
else
  info "Skipping Ollama (--skip-ollama)"
fi

# =============================================================================
# STEP 7 — Node.js + WhatsApp bridge
# =============================================================================
if [[ "$SKIP_WHATSAPP" == false ]]; then
  step "Node.js & WhatsApp bridge"

  NODE_OK=false
  if need node; then
    NODE_VER=$(node --version | tr -d 'v' | cut -d'.' -f1)
    if [[ "$NODE_VER" -ge 18 ]]; then
      ok "Node.js $(node --version) found"
      NODE_OK=true
    else
      warn "Node.js $(node --version) is too old (need ≥18)"
    fi
  fi

  if [[ "$NODE_OK" == false ]]; then
    info "Installing Node.js 20 LTS..."
    if [[ "$OS" == "linux" ]] && need curl; then
      curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - \
        && sudo apt-get install -y nodejs \
        && ok "Node.js $(node --version) installed" \
        || warn "Node.js install failed — WhatsApp bridge won't work"
    elif [[ "$OS" == "macos" ]] && need brew; then
      brew install node@20 && ok "Node.js installed" || warn "Node install failed"
    else
      warn "Install Node.js 18+ manually from https://nodejs.org"
    fi
  fi

  if need node; then
    WA_DIR="$SCRIPT_DIR/whatsapp"
    if [[ -d "$WA_DIR" && -f "$WA_DIR/package.json" ]]; then
      info "Installing WhatsApp bridge npm packages..."
      (cd "$WA_DIR" && npm install --silent) \
        && ok "WhatsApp bridge ready" \
        || warn "npm install failed in whatsapp/"
    else
      warn "whatsapp/ directory not found — skipping bridge setup"
    fi
  fi
else
  info "Skipping WhatsApp bridge (--skip-whatsapp)"
fi

# =============================================================================
# STEP 8 — .env setup
# =============================================================================
step ".env configuration"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  ok ".env already exists — not overwriting"
else
  if [[ -f "$SCRIPT_DIR/.env.example" ]]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    ok "Created .env from .env.example"
    warn "Edit .env and fill in your API keys before starting Jarvis!"
  else
    warn ".env.example not found — create .env manually"
  fi
fi

# =============================================================================
# STEP 9 — Verify key imports
# =============================================================================
step "Verifying key imports"

CHECKS=(
  "import ollama"
  "import fastapi"
  "import aiosqlite"
  "import faiss"
  "import sounddevice"
  "import speech_recognition"
  "import httpx"
  "import playwright"
  "import spotipy"
  "import discord"
  "import telegram"
)

ALL_OK=true
for check in "${CHECKS[@]}"; do
  PKG=$(echo "$check" | awk '{print $2}')
  if "$PYTHON_VENV" -c "$check" 2>/dev/null; then
    ok "  $PKG"
  else
    warn "  $PKG — NOT available"
    ALL_OK=false
  fi
done

# =============================================================================
# STEP 10 — Summary
# =============================================================================
echo ""
echo -e "${BOLD}${GREEN}══════════════════════════════════════════${RESET}"
echo -e "${BOLD}  Setup complete!${RESET}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${CYAN}To start Jarvis:${RESET}"
echo "    source .venv/bin/activate"
echo "    python main.py"
echo ""
echo -e "  ${CYAN}Dashboard:${RESET}  http://127.0.0.1:8550"
echo ""

if [[ ! -f "$SCRIPT_DIR/.env" ]] || grep -q "^JARVIS_LLM_PROVIDER=$" "$SCRIPT_DIR/.env" 2>/dev/null; then
  echo -e "  ${YELLOW}[!!] Don't forget to configure .env before starting!${RESET}"
  echo ""
fi

if [[ "$ALL_OK" == false ]]; then
  echo -e "  ${YELLOW}[!!] Some Python packages are missing — check warnings above.${RESET}"
  echo ""
fi
