#!/usr/bin/env bash
# =============================================================================
#  Jarvis — Zero-to-running setup script
#  Works on a brand-new machine with nothing installed.
#
#  Usage:
#    bash setup.sh                  # full setup
#    bash setup.sh --skip-ollama    # skip Ollama install / model pull
#    bash setup.sh --skip-whatsapp  # skip Node.js / WhatsApp bridge
#    bash setup.sh --skip-voice     # skip audio system packages
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
die()  { err "$*"; exit 1; }

# ── flags ─────────────────────────────────────────────────────────────────────
SKIP_OLLAMA=false
SKIP_WHATSAPP=false
SKIP_VOICE=false
for arg in "$@"; do
  [[ "$arg" == "--skip-ollama"    ]] && SKIP_OLLAMA=true
  [[ "$arg" == "--skip-whatsapp"  ]] && SKIP_WHATSAPP=true
  [[ "$arg" == "--skip-voice"     ]] && SKIP_VOICE=true
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════╗"
echo "  ║        Jarvis — Zero-to-Running          ║"
echo "  ╚══════════════════════════════════════════╝"
echo -e "${RESET}"

# ── detect OS ────────────────────────────────────────────────────────────────
OS="unknown"
if   [[ "$OSTYPE" == "linux-gnu"* ]];                               then OS="linux"
elif [[ "$OSTYPE" == "darwin"* ]];                                  then OS="macos"
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]];           then OS="windows"
elif [[ "${OS_ENV:-}" == "Windows_NT" ]];                           then OS="windows"
fi
info "Detected OS: $OS"

# ── helper: command exists? ───────────────────────────────────────────────────
need() { command -v "$1" &>/dev/null; }

# =============================================================================
# PRE-FLIGHT CHECKS
# =============================================================================
step "Pre-flight checks"

# 1. Internet connectivity
info "Checking internet connection..."
if curl -sf --max-time 5 https://pypi.org &>/dev/null \
|| curl -sf --max-time 5 https://google.com &>/dev/null; then
  ok "Internet is reachable"
else
  die "No internet connection detected. Connect to the internet and try again."
fi

# 2. sudo (Linux only)
if [[ "$OS" == "linux" ]]; then
  if need sudo; then
    # Test that sudo actually works (non-interactive)
    if sudo -n true 2>/dev/null; then
      ok "sudo available (passwordless)"
    else
      warn "sudo requires a password — you may be prompted during install"
    fi
  else
    die "sudo not found. Install it or run as root."
  fi
fi

# 3. Disk space — need at least 10 GB free
FREE_GB=99
if need df; then
  FREE_KB=$(df "$SCRIPT_DIR" | awk 'NR==2{print $4}')
  FREE_GB=$(( FREE_KB / 1024 / 1024 ))
fi
if [[ "$FREE_GB" -lt 10 ]]; then
  warn "Only ~${FREE_GB} GB free. Ollama models need several GB — make sure you have space."
else
  ok "Disk space: ~${FREE_GB} GB free"
fi

# =============================================================================
# STEP 1 — System packages
# =============================================================================
step "System packages"

if [[ "$OS" == "linux" ]]; then
  if ! need apt-get; then
    warn "apt-get not found — skipping system package install (not Debian/Ubuntu?)"
  else
    info "Running apt-get update..."
    sudo apt-get update -qq

    # --- base tools (needed before anything else) ---
    info "Installing base tools..."
    sudo apt-get install -y -qq \
      curl wget git \
      build-essential \
      software-properties-common \
      ca-certificates \
      gnupg \
      2>/dev/null && ok "Base tools ready"

    # --- Python build deps ---
    info "Installing Python build dependencies..."
    sudo apt-get install -y -qq \
      python3 python3-pip python3-venv python3-dev \
      libssl-dev libffi-dev \
      zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
      2>/dev/null && ok "Python build deps ready"

    # --- Audio (PyAudio + sounddevice) ---
    if [[ "$SKIP_VOICE" == false ]]; then
      info "Installing audio system libraries..."
      sudo apt-get install -y -qq \
        portaudio19-dev \
        libportaudio2 \
        libsndfile1 \
        libasound2-dev \
        ffmpeg \
        2>/dev/null && ok "Audio libraries ready"
    fi

    # --- OpenCV runtime ---
    info "Installing OpenCV runtime libraries..."
    sudo apt-get install -y -qq \
      libgl1 \
      libgl1-mesa-glx \
      libglib2.0-0 \
      libsm6 libxext6 libxrender-dev \
      2>/dev/null && ok "OpenCV runtime libs ready"

    # --- Playwright system dependencies (for Chromium) ---
    info "Installing Playwright/Chromium system dependencies..."
    sudo apt-get install -y -qq \
      libnss3 libatk1.0-0 libatk-bridge2.0-0 \
      libcups2 libdrm2 libdbus-1-3 \
      libxkbcommon0 libxcomposite1 libxdamage1 \
      libxfixes3 libxrandr2 libgbm1 libasound2 \
      libpango-1.0-0 libcairo2 \
      2>/dev/null && ok "Browser runtime libs ready"
  fi

elif [[ "$OS" == "macos" ]]; then
  if ! need brew; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
      && ok "Homebrew installed" \
      || die "Homebrew install failed. Install manually from https://brew.sh"
  else
    ok "Homebrew already installed"
  fi
  info "Installing system packages via Homebrew..."
  brew install portaudio libsndfile ffmpeg git 2>/dev/null || true
  ok "Homebrew packages done"

elif [[ "$OS" == "windows" ]]; then
  warn "Windows: some system libraries (PortAudio, ffmpeg) must be installed manually."
  warn "  PortAudio: https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio"
  warn "  ffmpeg:    https://ffmpeg.org/download.html"
fi

# =============================================================================
# STEP 2 — Python 3.11+
# =============================================================================
step "Python 3.11+"

find_python() {
  for cmd in python3.13 python3.12 python3.11 python3 python; do
    if need "$cmd"; then
      local ver major minor
      ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
      major="${ver%%.*}"
      minor="${ver##*.}"
      if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
        echo "$cmd"; return 0
      fi
    fi
  done
  return 1
}

PYTHON=""
PYTHON=$(find_python) || true

if [[ -z "$PYTHON" ]]; then
  warn "Python 3.11+ not found — attempting install..."

  if [[ "$OS" == "linux" ]] && need apt-get; then
    # Try plain apt first (Ubuntu 22.04+ has 3.11)
    sudo apt-get install -y -qq python3.11 python3.11-venv python3.11-dev 2>/dev/null || true
    PYTHON=$(find_python) || true

    # Fall back to deadsnakes PPA (Ubuntu 20.04 / older)
    if [[ -z "$PYTHON" ]]; then
      info "Adding deadsnakes PPA for Python 3.11..."
      sudo add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null \
        && sudo apt-get update -qq \
        && sudo apt-get install -y -qq python3.11 python3.11-venv python3.11-dev \
        && ok "Python 3.11 installed from deadsnakes PPA"
      PYTHON=$(find_python) || true
    fi
  elif [[ "$OS" == "macos" ]]; then
    brew install python@3.11 && PYTHON=$(find_python) || true
  fi

  [[ -z "$PYTHON" ]] && die "Could not install Python 3.11+. Install manually: https://www.python.org/downloads/"
fi

PY_VER=$("$PYTHON" --version)
ok "$PY_VER  →  $(which "$PYTHON")"

# =============================================================================
# STEP 3 — Virtual environment
# =============================================================================
step "Virtual environment"

VENV_DIR="$SCRIPT_DIR/.venv"

if [[ -d "$VENV_DIR" ]]; then
  ok "venv already exists at .venv — reusing"
else
  info "Creating virtual environment with $PYTHON..."
  "$PYTHON" -m venv "$VENV_DIR"
  ok "venv created at .venv/"
fi

source "$VENV_DIR/bin/activate"
PIP="$VENV_DIR/bin/pip"
PYTHON_VENV="$VENV_DIR/bin/python"

info "Upgrading pip / setuptools / wheel..."
"$PIP" install --quiet --upgrade pip setuptools wheel
ok "pip $(pip --version | awk '{print $2}') ready"

# =============================================================================
# STEP 4 — Python packages
# =============================================================================
step "Python packages"

REQS_FILE="$SCRIPT_DIR/requirements.txt"
TMP_REQS="$(mktemp /tmp/jarvis_reqs_XXXXXX.txt)"
trap 'rm -f "$TMP_REQS"' EXIT

if [[ "$OS" != "windows" ]]; then
  info "Filtering Windows-only packages (pywin32, pywinauto, pyautogui)..."
  grep -vE "^\s*(pywin32|pywinauto|pyautogui)" "$REQS_FILE" > "$TMP_REQS"
else
  cp "$REQS_FILE" "$TMP_REQS"
fi

info "Installing Python packages — this may take a few minutes..."

# Try bulk install first; fall back to one-by-one so a single bad package
# doesn't block everything else.
if ! "$PIP" install --quiet -r "$TMP_REQS" 2>/tmp/jarvis_pip_err.txt; then
  warn "Bulk install had errors — retrying package by package..."
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*#.*$ || -z "${line// }" ]] && continue
    pkg_name="${line%%[>=<!]*}"   # strip version specifier for display
    "$PIP" install --quiet "$line" 2>/dev/null \
      && ok "  ${pkg_name}" \
      || warn "  SKIP: ${pkg_name}  (non-fatal)"
  done < "$TMP_REQS"
fi

ok "Python packages done"

# =============================================================================
# STEP 5 — Playwright browsers + system deps
# =============================================================================
step "Playwright browsers"

PLAYWRIGHT="$VENV_DIR/bin/playwright"
if "$PYTHON_VENV" -c "import playwright" 2>/dev/null && [[ -f "$PLAYWRIGHT" ]]; then
  # install-deps pulls all OS libraries Chromium needs (the big one on fresh Linux)
  if [[ "$OS" == "linux" ]]; then
    info "Installing Playwright OS dependencies (chromium)..."
    "$PLAYWRIGHT" install-deps chromium 2>/dev/null \
      && ok "Playwright OS deps ready" \
      || warn "playwright install-deps failed — try: sudo playwright install-deps chromium"
  fi

  info "Downloading Playwright Chromium browser binary..."
  "$PLAYWRIGHT" install chromium 2>/dev/null \
    && ok "Playwright Chromium ready" \
    || warn "Playwright browser download failed — browser skills won't work"
else
  warn "Playwright not installed — skipping browser setup"
fi

# =============================================================================
# STEP 6 — Ollama
# =============================================================================
if [[ "$SKIP_OLLAMA" == false ]]; then
  step "Ollama (local LLM runtime)"

  if need ollama; then
    ok "Ollama already installed ($(ollama --version 2>/dev/null || echo 'version unknown'))"
  else
    info "Downloading Ollama installer..."
    if [[ "$OS" == "linux" || "$OS" == "macos" ]]; then
      curl -fsSL https://ollama.com/install.sh | sh \
        && ok "Ollama installed" \
        || warn "Ollama install failed — install manually from https://ollama.com"
    else
      warn "Windows: download Ollama from https://ollama.com/download/windows"
      warn "Then re-run setup.sh after installing Ollama."
    fi
  fi

  if need ollama; then
    # Start server in background if not already running
    if ! curl -sf --max-time 3 http://localhost:11434/api/tags &>/dev/null; then
      info "Starting Ollama server in background..."
      ollama serve >/tmp/ollama_setup.log 2>&1 &
      OLLAMA_BG_PID=$!
      # Wait up to 10s for it to come up
      for i in {1..10}; do
        sleep 1
        curl -sf --max-time 1 http://localhost:11434/api/tags &>/dev/null && break
      done
      curl -sf --max-time 1 http://localhost:11434/api/tags &>/dev/null \
        && ok "Ollama server running (PID $OLLAMA_BG_PID)" \
        || warn "Ollama server did not start in time — pull may fail"
    else
      ok "Ollama server already running"
    fi

    # Always pull the embedding model (required for Jarvis memory)
    info "Pulling embedding model: nomic-embed-text (required for memory)..."
    ollama pull nomic-embed-text \
      && ok "nomic-embed-text ready" \
      || warn "Failed to pull nomic-embed-text — memory features may not work"

    # Chat model
    CHAT_MODEL=""
    [[ -f "$SCRIPT_DIR/.env" ]] && \
      CHAT_MODEL=$(grep -E "^JARVIS_OLLAMA_MODEL=.+" "$SCRIPT_DIR/.env" \
                   | cut -d'=' -f2 | tr -d '"' | tr -d "'" | xargs 2>/dev/null) || true

    if [[ -n "$CHAT_MODEL" ]]; then
      info "Chat model already configured: $CHAT_MODEL — skipping pull"
    else
      echo ""
      echo -e "  ${BOLD}Recommended chat models:${RESET}"
      echo "    1) qwen2.5:7b   — fast, great Hebrew support  [recommended]"
      echo "    2) llama3.2:3b  — very fast, smaller, weaker Hebrew"
      echo "    3) qwen3:8b     — slower, more reasoning"
      echo "    4) Enter custom model name"
      echo ""
      read -rp "  Which model to pull? (1/2/3/name, or ENTER to skip): " MODEL_CHOICE
      case "$MODEL_CHOICE" in
        1|"") PULL_MODEL="qwen2.5:7b"  ;;
        2)    PULL_MODEL="llama3.2:3b"  ;;
        3)    PULL_MODEL="qwen3:8b"     ;;
        "")   PULL_MODEL=""             ;;
        *)    PULL_MODEL="$MODEL_CHOICE" ;;
      esac
      if [[ -n "$PULL_MODEL" && "$MODEL_CHOICE" != "" ]]; then
        info "Pulling $PULL_MODEL — this may take a while depending on your connection..."
        ollama pull "$PULL_MODEL" \
          && ok "$PULL_MODEL ready" \
          || warn "Failed to pull $PULL_MODEL"
      fi
    fi
  fi
else
  info "Skipping Ollama (--skip-ollama)"
fi

# =============================================================================
# STEP 7 — Node.js 20 LTS + WhatsApp bridge
# =============================================================================
if [[ "$SKIP_WHATSAPP" == false ]]; then
  step "Node.js 20 LTS + WhatsApp bridge"

  NODE_OK=false
  if need node; then
    NODE_MAJOR=$(node --version | tr -d 'v' | cut -d'.' -f1)
    if [[ "$NODE_MAJOR" -ge 18 ]]; then
      ok "Node.js $(node --version) found"
      NODE_OK=true
    else
      warn "Node.js $(node --version) is too old — need ≥18"
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
      brew install node@20 \
        && ok "Node.js installed" \
        || warn "Node.js install failed"

    else
      warn "Install Node.js 20 LTS manually from https://nodejs.org"
    fi
  fi

  if need node; then
    WA_DIR="$SCRIPT_DIR/whatsapp"
    if [[ -d "$WA_DIR" && -f "$WA_DIR/package.json" ]]; then
      info "Installing WhatsApp bridge npm packages..."
      (cd "$WA_DIR" && npm install --silent) \
        && ok "WhatsApp bridge (Baileys) ready" \
        || warn "npm install failed in whatsapp/ — WhatsApp bridge won't work"
    else
      warn "whatsapp/ directory not found — skipping WhatsApp bridge"
    fi
  fi
else
  info "Skipping WhatsApp bridge (--skip-whatsapp)"
fi

# =============================================================================
# STEP 8 — .env setup
# =============================================================================
step "Environment configuration (.env)"

ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"

if [[ -f "$ENV_FILE" ]]; then
  ok ".env already exists — not overwriting"
else
  if [[ -f "$ENV_EXAMPLE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    ok "Created .env from .env.example"
  else
    warn ".env.example not found — you must create .env manually"
  fi
fi

# =============================================================================
# STEP 9 — Verify key Python imports
# =============================================================================
step "Verifying key imports"

declare -A CHECKS=(
  ["ollama"]="ollama"
  ["fastapi"]="fastapi"
  ["aiosqlite"]="aiosqlite"
  ["faiss"]="faiss"
  ["sounddevice"]="sounddevice"
  ["speech_recognition"]="SpeechRecognition"
  ["httpx"]="httpx"
  ["playwright"]="playwright"
  ["spotipy"]="spotipy"
  ["discord"]="discord.py"
  ["telegram"]="python-telegram-bot"
  ["cv2"]="opencv-python"
  ["PIL"]="Pillow"
  ["numpy"]="numpy"
)

ALL_IMPORTS_OK=true
for module in "${!CHECKS[@]}"; do
  pkg="${CHECKS[$module]}"
  if "$PYTHON_VENV" -c "import $module" 2>/dev/null; then
    ok "  $pkg"
  else
    warn "  $pkg — MISSING (import $module failed)"
    ALL_IMPORTS_OK=false
  fi
done

# =============================================================================
# STEP 10 — Final summary
# =============================================================================
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║           Setup Complete!                ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════╝${RESET}"
echo ""
echo -e "${BOLD}  Next steps:${RESET}"
echo ""
echo -e "  ${CYAN}1. Configure your API keys:${RESET}"
echo "       nano .env"
echo "     Required at minimum:"
echo "       JARVIS_LLM_PROVIDER=ollama"
echo "       JARVIS_OLLAMA_MODEL=qwen2.5:7b"
echo "     Optional (for voice):"
echo "       JARVIS_ELEVENLABS_API_KEY=..."
echo "       JARVIS_VOICE_ENABLED=true"
echo ""
echo -e "  ${CYAN}2. Start Jarvis:${RESET}"
echo "       source .venv/bin/activate"
echo "       python main.py"
echo ""
echo -e "  ${CYAN}3. Open dashboard:${RESET}"
echo "       http://127.0.0.1:8550"
echo ""

if [[ "$ALL_IMPORTS_OK" == false ]]; then
  echo -e "  ${YELLOW}[!!] Some packages are missing — see warnings above.${RESET}"
  echo -e "       Usually fixed by re-running:  bash setup.sh"
  echo ""
fi

echo -e "  ${BOLD}Note:${RESET} Ollama must be running before you start Jarvis."
echo "       If it's not running:  ollama serve"
echo ""
