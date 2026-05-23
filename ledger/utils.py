from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from fastapi import Request

from config import DEFAULT_PUBLIC_LEDGER_URL


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def public_base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL")
    if configured:
        return configured.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        return f"{proto}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def safe_dashboard_return_path(value: str | None) -> str:
    if not value:
        return "/dashboard"
    text = unquote(str(value)).strip()
    parsed = urlsplit(text)
    if parsed.scheme or parsed.netloc:
        return "/dashboard"
    if parsed.path != "/dashboard":
        return "/dashboard"
    return urlunsplit(("", "", parsed.path, parsed.query, parsed.fragment))


def dashboard_return_path_with_query(value: str | None, updates: dict[str, str]) -> str:
    safe_path = safe_dashboard_return_path(value)
    parsed = urlsplit(safe_path)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(updates)
    return urlunsplit(("", "", parsed.path, urlencode(query), parsed.fragment))


def configured_public_base_url() -> str:
    configured = os.getenv("PUBLIC_BASE_URL")
    if configured and configured.strip():
        return configured.strip().rstrip("/")
    return DEFAULT_PUBLIC_LEDGER_URL


def dashboard_url(path_query: dict[str, str]) -> str:
    return f"{configured_public_base_url()}/dashboard?{urlencode(path_query)}"


def parse_positive_atomic(value: str) -> int:
    if not value.isdigit() or int(value) <= 0:
        raise ValueError("amountAtomic must be a positive integer string")
    return int(value)


def parse_dashboard_amount_atomic(entry: dict[str, Any], fallback: Decimal) -> Decimal:
    metadata = entry.get("metadata")
    if isinstance(metadata, dict):
        explicit = metadata.get("amountAtomic")
        if explicit is not None:
            return abs(atomic_decimal(explicit))
    return fallback


def parse_nonnegative_atomic(value: str) -> int:
    if not value.isdigit():
        raise ValueError("amountAtomic must be an integer string")
    return int(value)


def normalize_email(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def normalize_wallet_account_type(value: Any) -> Optional[str]:
    text = str(value or "").strip().upper()
    if text in {"EOA", "SCA"}:
        return text
    return None


def add_atomic(left: str, delta: int) -> str:
    result = int(left) + delta
    if result < 0:
        raise ValueError("ledger balance cannot become negative")
    return str(result)


def atomic_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def atomic_to_usdc(value: Any) -> float:
    return float(atomic_decimal(value) / Decimal("1000000"))


def decimal_usdc_to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return fallback


def decimal_usdc_to_atomic_string(value: Any) -> Optional[str]:
    try:
        atomic = Decimal(str(value)) * Decimal("1000000")
    except (InvalidOperation, ValueError):
        return None
    if atomic < 0:
        return None
    return str(int(atomic.to_integral_value()))


def short_address(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "ledger-account"
    if len(text) <= 18:
        return text
    return f"{text[:10]}...{text[-6:]}"


def claim_code_for_account(account: dict[str, Any], owner_email: str) -> str:
    agent_id = str(account.get("agentId") or "").strip()
    wallet_address = str(
        account.get("walletAddress") or account.get("circleWalletId") or ""
    ).strip()
    seed = f"{normalize_email(owner_email) or ''}:{agent_id}:{wallet_address}".encode(
        "utf-8"
    )
    return "clm_" + hashlib.sha256(seed).hexdigest()[:18]


def normalize_evm_address(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) != 42 or not text.startswith("0x"):
        raise ValueError("destinationAddress must be a 0x-prefixed EVM address")
    try:
        int(text[2:], 16)
    except ValueError as error:
        raise ValueError("destinationAddress must be a 0x-prefixed EVM address") from error
    return text
