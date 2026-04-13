"""OpenAI Codex OAuth — log in with your ChatGPT subscription (Plus/Pro/Team).

Uses the same public OAuth client as OpenClaw and Codex CLI.
Flow: OAuth 2.0 Authorization Code + PKCE → access token → Codex API.
Token is saved to disk and auto-refreshed.

Optional: ~/.codex/auth.json is used only if its JWT already contains **model.request**.
Plain `codex login` tokens usually do not — use **login codex** inside Jarvis for chat API.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

logger = logging.getLogger(__name__)

# OpenAI's public OAuth client for Codex (same one OpenClaw / codex-cli uses)
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_PORT = 8792
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
# Identity + API scopes. Do not omit profile/email — some OpenAI auth flows error without them.
# model.request / api.model.read / api.responses.write: required for chat + tools.
SCOPES = (
    "openid profile email offline_access "
    "model.request api.model.read api.responses.write "
    "api.connectors.read api.connectors.invoke"
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TOKEN_FILE = DATA_DIR / "codex_token.json"
CODEX_CLI_AUTH = Path.home() / ".codex" / "auth.json"


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    """Tiny HTTP handler that catches the OAuth callback."""

    auth_code: str | None = None
    state_received: str | None = None

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        _CallbackHandler.auth_code = qs.get("code", [None])[0]
        _CallbackHandler.state_received = qs.get("state", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
            b"<h1>&#10004; Jarvis connected to ChatGPT!</h1>"
            b"<p>You can close this window.</p>"
            b"</body></html>"
        )

    def log_message(self, *args):
        pass  # silence HTTP logs


def _jwt_payload(token: str) -> dict | None:
    """Decode JWT payload (no signature verify) — used for scope checks."""
    try:
        payload = token.split(".")[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None


def _jwt_exp(token: str) -> float | None:
    """Extract exp claim from a JWT without signature verification."""
    claims = _jwt_payload(token)
    if not claims:
        return None
    try:
        return float(claims.get("exp", 0))
    except (TypeError, ValueError):
        return None


def _token_allows_chat_completions(access_token: str) -> bool:
    """True only when the token can call OpenAI chat/responses APIs directly."""
    claims = _jwt_payload(access_token)
    if not claims:
        return False
    scp = claims.get("scp")
    if scp is None:
        scp = claims.get("scope") or ""
    if isinstance(scp, str):
        scopes = set(scp.split())
    elif isinstance(scp, list):
        scopes = set(scp)
    else:
        return False
    return "model.request" in scopes


def _save_token(token_data: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    token_data["saved_at"] = time.time()
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
    logger.info("Codex token saved to %s", TOKEN_FILE)


def _load_codex_cli_token() -> dict | None:
    """Read token from official codex-cli (~/.codex/auth.json) if it has API scopes."""
    if not CODEX_CLI_AUTH.exists():
        return None
    try:
        data = json.loads(CODEX_CLI_AUTH.read_text(encoding="utf-8"))
        tokens = data.get("tokens", {})
        access_token = tokens.get("access_token", "")
        if not access_token:
            return None
        if not _token_allows_chat_completions(access_token):
            logger.info(
                "Ignoring codex-cli token (%s) for OAuth API use: missing model.request in JWT — "
                "Jarvis will fall back to codex CLI unless you run `login codex` inside Jarvis.",
                CODEX_CLI_AUTH,
            )
            return None
        exp = _jwt_exp(access_token)
        now = time.time()
        expires_in = max(int(exp - now), 0) if exp else 864000
        logger.info("Loaded token from codex-cli (%s), expires_in=%ds", CODEX_CLI_AUTH, expires_in)
        return {
            "access_token": access_token,
            "refresh_token": tokens.get("refresh_token", ""),
            "expires_in": expires_in,
            "saved_at": now,
        }
    except Exception as e:
        logger.debug("Failed to read codex-cli auth: %s", e)
        return None


def _load_token() -> dict | None:
    if TOKEN_FILE.exists():
        try:
            raw = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            at = raw.get("access_token", "")
            if at and _token_allows_chat_completions(at):
                return raw
            logger.warning(
                "Ignoring %s: token lacks model.request — delete it and run `login codex` again.",
                TOKEN_FILE,
            )
        except Exception:
            pass
    return _load_codex_cli_token()


async def refresh_token(refresh_tok: str) -> dict | None:
    """Exchange a refresh_token for a new access_token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "client_id": OPENAI_CLIENT_ID,
            "refresh_token": refresh_tok,
        })
        if resp.status_code == 200:
            data = resp.json()
            data["refresh_token"] = data.get("refresh_token", refresh_tok)
            _save_token(data)
            logger.info("Codex token refreshed")
            return data
        else:
            logger.error("Token refresh failed: %s %s", resp.status_code, resp.text)
            return None


async def get_valid_token() -> str | None:
    """Return a valid access_token, refreshing if needed. Returns None if not logged in."""
    token_data = _load_token()
    if not token_data:
        return None

    access_token = token_data.get("access_token", "")
    expires_in = token_data.get("expires_in", 3600)
    saved_at = token_data.get("saved_at", 0)

    # Refresh if token expires within 5 minutes
    if time.time() > saved_at + expires_in - 300:
        refresh_tok = token_data.get("refresh_token")
        if refresh_tok:
            new_data = await refresh_token(refresh_tok)
            if new_data:
                at2 = new_data.get("access_token", "")
                if at2 and _token_allows_chat_completions(at2):
                    return at2
        return None

    if not _token_allows_chat_completions(access_token):
        logger.warning("Loaded token is missing model.request — OAuth login incomplete or stale.")
        return None

    return access_token


def is_logged_in() -> bool:
    """True only if we have an OAuth token that can call chat/responses APIs directly."""
    if TOKEN_FILE.exists():
        try:
            raw = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            at = raw.get("access_token", "")
            if at and _token_allows_chat_completions(at):
                return True
        except Exception:
            pass
    return False


async def login_interactive() -> dict | None:
    """Run the full OAuth PKCE login flow. Opens browser, waits for callback."""
    state = secrets.token_urlsafe(32)
    verifier, challenge = _generate_pkce()

    params = {
        "response_type": "code",
        "client_id": OPENAI_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Do NOT send "audience" here — it often triggers auth.openai.com unknown_error for this public client.
    }

    auth_url = f"{AUTH_URL}?{urlencode(params)}"

    # Start local HTTP server for callback
    _CallbackHandler.auth_code = None
    _CallbackHandler.state_received = None
    try:
        server = HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
    except OSError as e:
        logger.error("Cannot bind OAuth callback on 127.0.0.1:%s — %s", REDIRECT_PORT, e)
        print(
            f"\n  ✗ Port {REDIRECT_PORT} is busy. Close the app using it or edit REDIRECT_PORT in core/codex_auth.py\n"
        )
        return None
    server_thread = Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    print(f"\n  Opening browser to log in with ChatGPT...")
    print(f"  If the browser doesn't open, go to:\n  {auth_url}\n")
    print(
        "  If you see unknown_error: try another browser, disable VPN/adblock for openai.com.\n"
        "  Note: `codex login` alone does NOT grant chat API scopes — finish this Jarvis login.\n"
    )
    webbrowser.open(auth_url)

    # Wait for callback (up to 120 seconds)
    for _ in range(120):
        if _CallbackHandler.auth_code:
            break
        await asyncio.sleep(1)

    server.server_close()

    code = _CallbackHandler.auth_code
    if not code:
        logger.error("OAuth timed out — no authorization code received")
        return None

    if _CallbackHandler.state_received != state:
        logger.error("OAuth state mismatch!")
        return None

    # Exchange code for token
    async with httpx.AsyncClient() as client:
        resp = await client.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "client_id": OPENAI_CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        })

        if resp.status_code == 200:
            token_data = resp.json()
            at = token_data.get("access_token", "")
            if not _token_allows_chat_completions(at):
                logger.error("OAuth token missing model.request in JWT — scopes not granted")
                print(
                    "  ✗ ההתחברות הצליחה אבל הטוקן בלי הרשאת model.request ל-API. "
                    "נסה שוב או בדוק חשבון/ארגון ב-OpenAI."
                )
                return None
            _save_token(token_data)
            print("  ✓ Successfully connected to ChatGPT!")
            return token_data
        else:
            logger.error("Token exchange failed: %s %s", resp.status_code, resp.text)
            print(f"  ✗ Login failed: {resp.status_code}")
            return None
