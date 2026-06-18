from __future__ import annotations

import base64
import threading
import time
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_SKEW_SECONDS = 300

_nonce_lock = threading.Lock()
_seen_nonces: dict[str, float] = {}


def _b64url_decode(value: str) -> bytes:
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def public_key_is_valid(public_key_b64: str) -> bool:
    try:
        raw = _b64url_decode(public_key_b64)
        Ed25519PublicKey.from_public_bytes(raw)
        return True
    except Exception:
        return False


def signing_message(*, agent_id: str, timestamp: str, nonce: str, body: str) -> str:
    return f"{agent_id}\n{timestamp}\n{nonce}\n{body}"


def verify_agent_signature(
    *,
    public_key_b64: str,
    agent_id: str,
    timestamp: str,
    nonce: str,
    body: str,
    signature_b64: str,
) -> bool:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(_b64url_decode(public_key_b64))
        signature = _b64url_decode(signature_b64)
    except Exception:
        return False
    message = signing_message(
        agent_id=agent_id, timestamp=timestamp, nonce=nonce, body=body
    ).encode("utf-8")
    try:
        public_key.verify(signature, message)
        return True
    except InvalidSignature:
        return False


def _parse_epoch(timestamp_iso: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def check_timestamp_and_nonce(
    timestamp_iso: str, nonce: str, *, now_epoch: float | None = None
) -> bool:
    current = now_epoch if now_epoch is not None else time.time()
    request_epoch = _parse_epoch(timestamp_iso)
    if request_epoch is None or abs(current - request_epoch) > MAX_SKEW_SECONDS:
        return False
    if not nonce:
        return False
    with _nonce_lock:
        for seen_nonce, expires_at in list(_seen_nonces.items()):
            if expires_at < current:
                del _seen_nonces[seen_nonce]
        if nonce in _seen_nonces:
            return False
        _seen_nonces[nonce] = current + MAX_SKEW_SECONDS
    return True


def reset_nonce_cache() -> None:
    with _nonce_lock:
        _seen_nonces.clear()
