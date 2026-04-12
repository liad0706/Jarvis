"""Jarvis configuration loaded from .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_prefix="JARVIS_",
        extra="ignore",
    )

    # LLM Provider: "ollama", "lm_studio" (OpenAI API on localhost:1234), "openai", "codex", "anthropic" / "claude"
    llm_provider: str = "ollama"
    # Max seconds for one LLM round-trip (streaming or chat). Local Ollama often needs >120s on first load / large models.
    llm_call_timeout_seconds: float = 300.0

    # Ollama (free, local)
    # Prefer 127.0.0.1 - some Windows setups resolve "localhost" oddly vs WSL/Docker
    ollama_host: str = "http://127.0.0.1:11434"
    # Default when .env omits JARVIS_OLLAMA_MODEL. Prefer qwen2.5:7b for speed+tools; qwen3:8b if you need max quality.
    ollama_model: str = "qwen2.5:7b"
    # Speed tuning (optional). num_predict caps output tokens → shorter generations = faster. Omit for Ollama default (unlimited).
    ollama_num_predict: int | None = None
    # Smaller context = faster prefill; too low may truncate long system prompts + tools.
    ollama_num_ctx: int | None = None
    # Keep model loaded between requests, e.g. "15m" or "0" (forever). Reduces cold-start on each message.
    ollama_keep_alive: str = ""
    # Layer offload: -1 = as many layers on GPU as Ollama fits; 0 = CPU only; omit = Ollama default.
    # Requires Ollama build + drivers that expose a GPU backend (NVIDIA common on Windows; AMD varies).
    ollama_num_gpu: int | None = None
    # When False, routed Codex/OpenAI/Claude requests will not retry through Ollama.
    ollama_fallback_enabled: bool = True

    # OpenAI / Codex (requires API key)
    openai_api_key: str = ""
    openai_model: str = "gpt-5.4"
    # `codex exec` + ChatGPT subscription: gpt-4o-mini etc. are rejected (400). Use gpt-5.4 or another Codex-supported id.
    codex_cli_model: str = "gpt-5.4"
    openai_base_url: str = ""  # leave empty for default, or set for compatible APIs

    # Anthropic / Claude (requires API key)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    # Spotify
    spotipy_client_id: str = ""
    spotipy_client_secret: str = ""
    spotipy_redirect_uri: str = "http://localhost:8888/callback"

    # Creality Print (desktop app)
    creality_print_exe: str = "C:/Program Files/Creality Print/CrealityPrint.exe"
    # Creality K1 Max direct API (Moonraker) — also readable from CREALITY_PRINTER_IP env var
    creality_printer_ip: str = ""

    # Barber / Calmark (Playwright)
    kamarlek_barber_name: str = "ישי פרץ"
    calmark_barber_url: str = "https://calmark.co.il/p/YRVLZ"
    # headless=false opens a real Chromium window so you can watch automation
    calmark_playwright_headless: bool = True
    calmark_playwright_slow_mo_ms: int = 0  # e.g. 150 to slow clicks for visibility

    # Paths
    stl_download_dir: str = str(PROJECT_ROOT / "data" / "downloads")
    code_output_dir: str = str(PROJECT_ROOT / "data" / "generated_code")
    # code skill: stubs + Cursor only - no Settings switch; LLMs never fill implementation in those files.

    # RuView — WiFi human sensing (presence, vitals, pose)
    ruview_enabled: bool = False
    ruview_url: str = "http://localhost:3000"  # RuView Rust sensing server
    ruview_poll_interval_seconds: float = 5.0  # how often to poll for presence changes
    ruview_auto_lights: bool = True  # smart room automation (lights on entry, etc.)

    # Home Assistant
    ha_url: str = "http://localhost:8123"
    ha_token: str = ""

    # Apple TV (LAN IP + optional credentials file for pyatv)
    apple_tv_host: str = ""
    apple_tv_credentials_file: str = str(PROJECT_ROOT / "data" / "apple_tv.conf")

    # Voice
    voice_enabled: bool = False

    # Security / Policy
    production_mode: bool = False
    sandbox_enabled: bool = True
    safe_mode: bool = False
    dry_run: bool = False
    # When True (default), Spotify / web / etc. run without y/n prompts (avoids EOFError in non-TTY).
    auto_approve_external: bool = True
    allowed_packages: str = "requests,beautifulsoup4,pillow,numpy,pandas,matplotlib"

    # WhatsApp
    whatsapp_enabled: bool = False
    whatsapp_allowed_numbers: str = ""
    whatsapp_api_port: int = 8585
    whatsapp_allow_groups: bool = False

    # Dashboard
    dashboard_enabled: bool = True
    dashboard_port: int = 8550
    dashboard_host: str = "0.0.0.0"

    # Development auto-reload: watch source files and do a fresh process restart.
    dev_auto_reload: bool = True
    dev_auto_reload_poll_seconds: float = 1.0
    dev_auto_reload_quiet_seconds: float = 1.5

    # System prompt style: "default", "explanatory", "learning"
    system_prompt_style: str = "default"

    # Memory / Embeddings - EmbeddingEngine tries fallbacks if this name 404s
    embedding_model: str = "nomic-embed-text"
    summarize_threshold: int = 40

    # Memory retention (days). Set to 0 to disable pruning for that table.
    memory_conversations_keep_days: int = 30
    memory_episodic_keep_days: int = 90
    memory_embeddings_keep_days: int = 90

    # FAISS + Hybrid Search
    faiss_enabled: bool = True  # Enable FAISS-backed hybrid search

    # Discord
    discord_token: str = ""
    discord_allowed_users: str = ""  # comma-separated user IDs
    discord_allowed_channels: str = ""  # comma-separated channel IDs

    # Telegram
    telegram_token: str = ""
    telegram_allowed_users: str = ""  # comma-separated user IDs or usernames

    # OpenAI-compatible API server
    api_server_enabled: bool = False
    api_server_port: int = 8600
    api_server_host: str = "0.0.0.0"

    # Pushover (iPhone push notifications)
    pushover_user_key: str = ""   # JARVIS_PUSHOVER_USER_KEY
    pushover_app_token: str = ""  # JARVIS_PUSHOVER_APP_TOKEN

    # Telemetry
    telemetry_enabled: bool = True

    # Learning system
    learning_enabled: bool = True

    # Browser agent
    browser_headless: bool = True

    # Code interpreter
    code_interpreter_prefer_docker: bool = True


def ollama_runtime_options(settings: Any) -> dict[str, Any]:
    """Ollama API `options` (num_gpu, context length, etc.) for chat/embed.

    Use with every direct ``client.chat`` / ``client.embed`` so behavior matches
    :class:`OllamaProvider` (GPU offload, caps). ``num_gpu=-1`` = max layers on GPU.
    """
    extra: dict[str, Any] = {}
    np = getattr(settings, "ollama_num_predict", None)
    if np is not None and int(np) > 0:
        extra["num_predict"] = int(np)
    nctx = getattr(settings, "ollama_num_ctx", None)
    if nctx is not None and int(nctx) > 0:
        extra["num_ctx"] = int(nctx)
    ng = getattr(settings, "ollama_num_gpu", None)
    if ng is not None:
        extra["num_gpu"] = int(ng)
    return extra


@lru_cache
def get_settings() -> Settings:
    return Settings()
