from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode


@dataclass
class OAuthState:
    state: str
    code_verifier: str
    code_challenge: str


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _base64url_no_padding(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def build_github_oauth_state() -> OAuthState:
    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return OAuthState(
        state=state,
        code_verifier=code_verifier,
        code_challenge=_base64url_no_padding(digest),
    )


def build_github_login_url(oauth_state: OAuthState) -> str:
    client_id = require_env("GITHUB_CLIENT_ID")
    public_base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": f"{public_base_url}/auth/github/callback",
            "scope": "read:user user:email",
            "state": oauth_state.state,
            "code_challenge": oauth_state.code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"https://github.com/login/oauth/authorize?{query}"


def sign_session(payload: dict[str, str]) -> str:
    secret = require_env("AUTH_SESSION_SECRET")
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = _base64url_no_padding(raw_body)
    signature = _base64url_no_padding(
        hmac.new(
            secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
        ).digest()
    )
    return f"{body}.{signature}"


def verify_session(value: Optional[str]) -> Optional[dict[str, str]]:
    if not value:
        return None

    secret = require_env("AUTH_SESSION_SECRET")
    try:
        body, signature = value.split(".", 1)
    except ValueError:
        return None

    expected_signature = _base64url_no_padding(
        hmac.new(
            secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
        ).digest()
    )
    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        padded_body = body + "=" * (-len(body) % 4)
        raw_payload = base64.urlsafe_b64decode(padded_body.encode("ascii"))
        payload = json.loads(raw_payload.decode("utf-8"))
    except (ValueError, TypeError):
        return None

    if not isinstance(payload, dict):
        return None
    if not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in payload.items()
    ):
        return None
    return payload
