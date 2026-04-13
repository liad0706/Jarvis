import base64
import json

from core.codex_auth import _token_allows_chat_completions


def _jwt_with_scopes(scopes):
    header = {"alg": "none", "typ": "JWT"}
    payload = {"scp": scopes}

    def enc(obj):
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{enc(header)}.{enc(payload)}."


def test_codex_auth_requires_model_request_scope():
    token = _jwt_with_scopes(["api.connectors.invoke"])
    assert _token_allows_chat_completions(token) is False

    token = _jwt_with_scopes(["model.request", "api.connectors.invoke"])
    assert _token_allows_chat_completions(token) is True
