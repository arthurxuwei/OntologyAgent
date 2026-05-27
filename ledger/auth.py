from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from config import (
    ADMIN_COOKIE,
    AUTH_SESSION_MAX_AGE_SECONDS,
    GITHUB_EMAILS_URL,
    GITHUB_TOKEN_URL,
    GITHUB_USER_URL,
    OAUTH_RETURN_COOKIE,
    OAUTH_STATE_COOKIE,
    SESSION_COOKIE,
)
from utils import (
    dashboard_return_path_with_query,
    normalize_email,
    public_base_url,
    require_env,
    safe_dashboard_return_path,
)


def sign_auth_session(user: dict[str, Any]) -> str:
    secret = require_env("AUTH_SESSION_SECRET").encode("utf-8")
    payload = json.dumps(user, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    signature = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_auth_session(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        body, signature = value.split(".", 1)
        secret = require_env("AUTH_SESSION_SECRET").encode("utf-8")
    except (RuntimeError, ValueError):
        return None
    expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    padded = body + ("=" * (-len(body) % 4))
    try:
        parsed = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict) or parsed.get("provider") != "github":
        return None
    email = normalize_email(parsed.get("email"))
    if not email:
        return None
    parsed["email"] = email
    return parsed


def configured_admin_token() -> str | None:
    token = os.getenv("ADMIN_TOKEN")
    if token and token.strip():
        return token.strip()
    return None


def admin_token_matches(value: str | None) -> bool:
    configured = configured_admin_token()
    if not configured or value is None:
        return False
    return hmac.compare_digest(configured, value)


def sign_admin_session() -> str:
    configured = configured_admin_token()
    if not configured:
        raise RuntimeError("ADMIN_TOKEN is required")
    token = configured.encode("utf-8")
    body = (
        base64.urlsafe_b64encode(ADMIN_COOKIE.encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    signature = hmac.new(token, body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_admin_session(value: str | None) -> bool:
    configured = configured_admin_token()
    if not configured or not value:
        return False
    try:
        body, signature = value.split(".", 1)
    except ValueError:
        return False
    expected = hmac.new(
        configured.encode("utf-8"),
        body.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False
    padded = body + ("=" * (-len(body) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except ValueError:
        return False
    return hmac.compare_digest(decoded, ADMIN_COOKIE)


async def fetch_github_user(code: str, redirect_uri: str | None = None) -> dict[str, Any]:
    client_id = require_env("GITHUB_CLIENT_ID")
    client_secret = require_env("GITHUB_CLIENT_SECRET")
    token_request = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
    }
    if redirect_uri:
        token_request["redirect_uri"] = redirect_uri
    async with httpx.AsyncClient(timeout=20) as client:
        token_response = await client.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data=token_request,
        )
        token_response.raise_for_status()
        token_payload = token_response.json()
        access_token = token_payload.get("access_token")
        if not access_token:
            raise RuntimeError("GitHub OAuth token exchange did not return access_token")

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        user_response = await client.get(GITHUB_USER_URL, headers=headers)
        user_response.raise_for_status()
        profile = user_response.json()

        email = normalize_email(profile.get("email"))
        if not email:
            emails_response = await client.get(GITHUB_EMAILS_URL, headers=headers)
            emails_response.raise_for_status()
            for item in emails_response.json():
                if not isinstance(item, dict):
                    continue
                candidate = normalize_email(item.get("email"))
                if candidate and item.get("primary") and item.get("verified"):
                    email = candidate
                    break
            if not email:
                for item in emails_response.json():
                    if isinstance(item, dict) and item.get("verified"):
                        email = normalize_email(item.get("email"))
                        if email:
                            break

    if not email:
        raise RuntimeError("GitHub account does not expose a verified email")
    login = str(profile.get("login") or "").strip()
    return {
        "provider": "github",
        "login": login,
        "name": str(profile.get("name") or login or email).strip(),
        "email": email,
        "avatar_url": profile.get("avatar_url"),
    }


async def complete_github_callback(
    request: Request,
    *,
    code: str = "",
    state: str = "",
    error: str = "",
    stored_state: str | None = None,
    stored_return: str | None = None,
) -> RedirectResponse:
    if error:
        response = RedirectResponse(
            dashboard_return_path_with_query(stored_return, {"auth_error": error}),
            status_code=307,
        )
        response.delete_cookie(OAUTH_STATE_COOKIE)
        response.delete_cookie(OAUTH_RETURN_COOKIE)
        return response
    if not code or not state or not stored_state or not hmac.compare_digest(state, stored_state):
        raise HTTPException(status_code=400, detail="Invalid GitHub OAuth state")
    try:
        user = await fetch_github_user(code)
        session_value = sign_auth_session(user)
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    response = RedirectResponse(safe_dashboard_return_path(stored_return), status_code=307)
    response.set_cookie(
        SESSION_COOKIE,
        session_value,
        httponly=True,
        samesite="lax",
        secure=public_base_url(request).startswith("https://"),
        max_age=AUTH_SESSION_MAX_AGE_SECONDS,
    )
    response.delete_cookie(OAUTH_STATE_COOKIE)
    response.delete_cookie(OAUTH_RETURN_COOKIE)
    return response
