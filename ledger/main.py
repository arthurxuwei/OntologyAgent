from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import threading
import time
import uuid
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlencode

import httpx
import jwt
from chain_client import ChainHttpClient
from circle_client import CircleHttpClient
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from mcp_tools import (
    agent_wallet_create_escrow_tool,
    agent_wallet_create_onramp_session_tool,
    agent_wallet_get_or_create_tool,
    agent_wallet_get_ledger_state_tool,
    agent_wallet_refund_escrow_tool,
    agent_wallet_release_escrow_tool,
    agent_wallet_transfer_tool,
    build_mcp_app,
    route_payment_intent_tool,
)
from payment_router import PaymentIntent, route_payment_intent
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


DEFAULT_ASSET = "USDC"
DEFAULT_LEDGER_STATE_PATH = "ledger/data/offchain_ledger.json"
DEFAULT_COINBASE_API_BASE_URL = "https://api.developer.coinbase.com"
DEFAULT_COINBASE_TOKEN_PATH = "/onramp/v1/token"
DEFAULT_COINBASE_HOSTED_URL = "https://pay.coinbase.com/buy/select-asset"
DEFAULT_CHAIN_HTTP_URL = "http://chain:8091"
DEFAULT_SETTLEMENT_HTTP_URL = "http://circle:8093"
DEFAULT_WALLET_HTTP_URL = "http://circle:8093"
DEFAULT_CHAIN_RECORDER_ADDRESS = "0x000000000000000000000000000000000000dEaD"
LEDGER_CONSOLE_PATH = Path(__file__).resolve().parent / "web" / "index.html"
LEDGER_DASHBOARD_PATH = Path(__file__).resolve().parent / "web" / "dashboard.html"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

app = FastAPI(title="Chief offchain ledger")
logger = logging.getLogger("chief.ledger")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LedgerAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agentId: str
    agentName: Optional[str] = None
    email: Optional[str] = None
    walletAddress: Optional[str] = None
    circleWalletId: Optional[str] = None
    asset: str = DEFAULT_ASSET
    availableAtomic: str = "0"
    lockedAtomic: str = "0"
    createdAt: str
    updatedAt: str


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entryId: str
    entryType: Literal[
        "credit",
        "escrow_lock",
        "escrow_release",
        "escrow_refund",
        "agent_transfer",
        "withdrawal",
    ]
    agentId: str
    asset: str = DEFAULT_ASSET
    availableDeltaAtomic: str = "0"
    lockedDeltaAtomic: str = "0"
    escrowId: Optional[str] = None
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    createdAt: str


class LedgerChainRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recordId: str
    eventType: Literal[
        "credit",
        "escrow_lock",
        "escrow_release",
        "escrow_refund",
        "agent_transfer",
        "withdrawal",
    ]
    status: Literal["submitted", "failed"]
    chainTool: str = "chain_submit_execution"
    chainMcpUrl: str
    recorderAddress: str
    txHash: Optional[str] = None
    mode: Optional[str] = None
    escrowId: Optional[str] = None
    entryIds: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    toolResult: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    createdAt: str
    updatedAt: str


class LedgerSettlementRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recordId: str
    eventType: Literal["escrow_release", "agent_transfer", "withdrawal"]
    status: Literal["submitted", "failed"]
    settlementTool: str = "agent_wallet_settle_ledger_transfer"
    chainMcpUrl: str
    escrowId: Optional[str] = None
    transferId: Optional[str] = None
    fromAgentId: str
    toAgentId: Optional[str] = None
    toAddress: Optional[str] = None
    asset: str = DEFAULT_ASSET
    amountAtomic: str
    transactionId: Optional[str] = None
    transactionHash: Optional[str] = None
    transactionState: Optional[str] = None
    mode: Optional[str] = None
    toolResult: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    createdAt: str
    updatedAt: str


class EscrowRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    escrowId: str
    buyerAgentId: str
    sellerAgentId: str
    amountAtomic: str
    asset: str = DEFAULT_ASSET
    status: Literal["locked", "released", "refunded"]
    taskId: Optional[str] = None
    description: Optional[str] = None
    createdAt: str
    updatedAt: str
    releasedAt: Optional[str] = None
    refundedAt: Optional[str] = None


class OnrampSessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    provider: Literal["coinbase"] = "coinbase"
    providerToken: Optional[str] = None
    providerChannelId: Optional[str] = None
    providerOrderId: Optional[str] = None
    agentId: str
    destinationAddress: str
    destinationNetwork: str = "base"
    purchaseCurrency: str = DEFAULT_ASSET
    paymentCurrency: str = "USD"
    paymentAmount: str
    clientIp: str
    partnerUserRef: Optional[str] = None
    redirectUrl: Optional[str] = None
    defaultPaymentMethod: Optional[str] = None
    idempotencyKey: str
    onrampUrl: str
    status: Literal[
        "created",
        "opened",
        "pending",
        "confirming",
        "credited",
        "failed",
        "expired",
        "cancelled",
    ] = "created"
    creditedAmountAtomic: Optional[str] = None
    txHash: Optional[str] = None
    ledgerEntryId: Optional[str] = None
    createdAt: str
    updatedAt: str
    creditedAt: Optional[str] = None


class OnrampEventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str
    sessionId: str
    provider: Literal["coinbase"] = "coinbase"
    eventType: str
    providerEventId: Optional[str] = None
    rawPayload: dict[str, Any] = Field(default_factory=dict)
    createdAt: str


class LedgerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounts: list[LedgerAccount] = Field(default_factory=list)
    entries: list[LedgerEntry] = Field(default_factory=list)
    escrows: list[EscrowRecord] = Field(default_factory=list)
    onrampSessions: list[OnrampSessionRecord] = Field(default_factory=list)
    onrampEvents: list[OnrampEventRecord] = Field(default_factory=list)
    chainRecords: list[LedgerChainRecord] = Field(default_factory=list)
    settlementRecords: list[LedgerSettlementRecord] = Field(default_factory=list)


class CreditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amountAtomic: str
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentWalletRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentName: str
    agentId: str
    email: Optional[str] = None
    walletAddress: Optional[str] = None
    circleWalletId: Optional[str] = None
    agentDescription: Optional[str] = None


class GatewayDepositRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentId: str = Field(min_length=1)
    amountAtomic: str = Field(min_length=1)
    refId: Optional[str] = None


class GatewayWithdrawalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentId: str = Field(min_length=1)
    amountAtomic: str = Field(min_length=1)
    recipientAddress: Optional[str] = None
    refId: Optional[str] = None

    @field_validator("recipientAddress")
    @classmethod
    def validate_recipient_address(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_evm_address(value)


class CreateEscrowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    buyerAgentId: str
    sellerAgentId: str
    amountAtomic: str
    taskId: Optional[str] = None
    description: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentTransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fromEmail: str = Field(min_length=1)
    toEmail: str = Field(min_length=1)
    amountAtomic: str = Field(min_length=1)
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WithdrawalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentId: str = Field(min_length=1)
    destinationAddress: str = Field(min_length=1)
    amountAtomic: str = Field(min_length=1)
    ownerEmail: Optional[str] = None
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("destinationAddress")
    @classmethod
    def validate_destination_address(cls, value: str) -> str:
        return normalize_evm_address(value)


class CreateOnrampSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentId: str = Field(min_length=1)
    destinationAddress: str = Field(min_length=1)
    paymentAmount: str = Field(min_length=1)
    idempotencyKey: str = Field(min_length=1)
    clientIp: str = "192.0.2.1"
    destinationNetwork: str = "base"
    purchaseCurrency: str = DEFAULT_ASSET
    paymentCurrency: str = "USD"
    partnerUserRef: Optional[str] = None
    redirectUrl: Optional[HttpUrl] = None
    defaultPaymentMethod: Optional[str] = None

    @field_validator("paymentAmount")
    @classmethod
    def validate_payment_amount(cls, value: str) -> str:
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("paymentAmount must be a positive decimal string") from error
        if parsed <= 0:
            raise ValueError("paymentAmount must be a positive decimal string")
        return value


class ConfirmOnrampSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    providerOrderId: str = Field(min_length=1)
    amountAtomic: str = Field(min_length=1)
    txHash: Optional[str] = None
    providerEventId: Optional[str] = None
    rawPayload: dict[str, Any] = Field(default_factory=dict)


def parse_positive_atomic(value: str) -> int:
    if not value.isdigit() or int(value) <= 0:
        raise ValueError("amountAtomic must be a positive integer string")
    return int(value)


def parse_nonnegative_atomic(value: str) -> int:
    if not value.isdigit():
        raise ValueError("amountAtomic must be an integer string")
    return int(value)


def normalize_email(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


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


def normalize_evm_address(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) != 42 or not text.startswith("0x"):
        raise ValueError("destinationAddress must be a 0x-prefixed EVM address")
    try:
        int(text[2:], 16)
    except ValueError as error:
        raise ValueError("destinationAddress must be a 0x-prefixed EVM address") from error
    return text


def dashboard_counterparty(
    entry: dict[str, Any],
    escrow_by_id: dict[str, dict[str, Any]],
) -> str:
    metadata = entry.get("metadata")
    if isinstance(metadata, dict):
        counterparty_email = metadata.get("counterpartyEmail")
        if isinstance(counterparty_email, str) and counterparty_email.strip():
            return counterparty_email.strip()

        from_email = metadata.get("fromEmail")
        to_email = metadata.get("toEmail")
        if (
            entry.get("entryType") == "agent_transfer"
            and isinstance(from_email, str)
            and from_email.strip()
            and isinstance(to_email, str)
            and to_email.strip()
        ):
            available_delta = atomic_decimal(entry.get("availableDeltaAtomic"))
            return to_email.strip() if available_delta < 0 else from_email.strip()

        for key in (
            "counterpartyAgentId",
            "counterpartyAgentName",
            "counterparty",
        ):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    escrow_id = entry.get("escrowId")
    if isinstance(escrow_id, str) and escrow_id in escrow_by_id:
        escrow = escrow_by_id[escrow_id]
        agent_id = entry.get("agentId")
        buyer_id = escrow.get("buyerAgentId")
        seller_id = escrow.get("sellerAgentId")
        if agent_id == buyer_id and seller_id:
            return str(seller_id)
        if agent_id == seller_id and buyer_id:
            return str(buyer_id)
        return str(escrow.get("description") or escrow_id)
    entry_type = str(entry.get("entryType") or "")
    reason = str(entry.get("reason") or "").strip()
    if "onramp" in entry_type or "onramp" in reason.lower():
        return "Coinbase Onramp"
    return reason or "Ledger"


def dashboard_transaction(
    entry: dict[str, Any],
    escrow_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    entry_type = str(entry.get("entryType") or "ledger")
    available_delta = atomic_decimal(entry.get("availableDeltaAtomic"))
    locked_delta = atomic_decimal(entry.get("lockedDeltaAtomic"))
    escrow = escrow_by_id.get(str(entry.get("escrowId") or ""))
    amount_atomic = (
        atomic_decimal(escrow.get("amountAtomic"))
        if escrow
        else max(abs(available_delta), abs(locked_delta))
    )
    agent_id = entry.get("agentId")
    direction = "out" if available_delta < 0 or locked_delta > 0 else "in"
    if entry_type == "escrow_release" and escrow and agent_id == escrow.get("buyerAgentId"):
        direction = "out"
    status = "released"
    if entry_type == "escrow_lock":
        status = "locked"
    elif entry_type == "escrow_refund":
        status = "refunded"
    elif "onramp" in entry_type or entry_type == "credit":
        status = "onramp"
    role = "payer" if direction == "out" else "payee"
    if entry_type == "withdrawal":
        role = "withdrawal"
    elif status == "onramp":
        role = "deposit"
    elif status == "refunded":
        role = "refund"
    return {
        "id": entry.get("entryId") or entry.get("escrowId") or "ledger_entry",
        "counterparty": dashboard_counterparty(entry, escrow_by_id),
        "amount": atomic_to_usdc(amount_atomic),
        "direction": direction,
        "role": role,
        "status": status,
        "timestamp": entry.get("createdAt") or "ledger",
    }


def empty_dashboard_agent(account: dict[str, Any]) -> dict[str, Any]:
    agent_id = str(account.get("agentId") or "").strip()
    wallet_address = (
        account.get("walletAddress")
        or account.get("circleWalletId")
        or agent_id
    )
    return {
        "agent": {
            "id": agent_id,
            "name": str(account.get("agentName") or agent_id),
            "role": "Agent Wallet Account",
            "walletAddress": short_address(wallet_address),
            "fullWalletAddress": str(wallet_address),
            "claimedDaysAgo": 0,
            "ownerEmail": normalize_email(account.get("email")),
        },
        "balance": {
            "available": 0.0,
            "locked": 0.0,
            "lifetimeIn": 0.0,
            "lifetimeOut": 0.0,
        },
        "transactions": [],
        "settings": {"limits": {"perTradeCap": 0.01}},
    }


def build_dashboard_data(
    ledger_state: dict[str, Any],
    owner_email: Optional[str] = None,
) -> dict[str, Any]:
    accounts = [
        account
        for account in ledger_state.get("accounts", [])
        if isinstance(account, dict)
    ]
    entries = [
        entry
        for entry in ledger_state.get("entries", [])
        if isinstance(entry, dict)
    ]
    escrows = [
        escrow
        for escrow in ledger_state.get("escrows", [])
        if isinstance(escrow, dict)
    ]
    escrow_by_id = {
        str(escrow.get("escrowId")): escrow
        for escrow in escrows
        if escrow.get("escrowId")
    }
    normalized_owner_email = normalize_email(owner_email)
    entries_by_agent: dict[str, list[dict[str, Any]]] = {}
    for entry in sorted(entries, key=lambda item: str(item.get("createdAt") or ""), reverse=True):
        agent_id = str(entry.get("agentId") or "").strip()
        if not agent_id:
            continue
        entries_by_agent.setdefault(agent_id, []).append(entry)

    agents: dict[str, Any] = {}
    for account in accounts:
        agent_id = str(account.get("agentId") or "").strip()
        if not agent_id:
            continue
        if normalized_owner_email and normalize_email(account.get("email")) != normalized_owner_email:
            continue
        agent_entries = entries_by_agent.get(agent_id, [])
        lifetime_in = sum(
            atomic_to_usdc(entry.get("availableDeltaAtomic"))
            for entry in agent_entries
            if atomic_decimal(entry.get("availableDeltaAtomic")) > 0
        )
        lifetime_out = sum(
            atomic_to_usdc(abs(atomic_decimal(entry.get("availableDeltaAtomic"))))
            for entry in agent_entries
            if atomic_decimal(entry.get("availableDeltaAtomic")) < 0
        )
        wallet_address = (
            account.get("walletAddress")
            or account.get("circleWalletAddress")
            or account.get("circleWalletId")
            or agent_id
        )
        agents[agent_id] = {
            "agent": {
                "id": agent_id,
                "name": str(account.get("agentName") or agent_id),
                "role": "Agent Wallet Account",
                "walletAddress": short_address(wallet_address),
                "fullWalletAddress": str(wallet_address),
                "claimedDaysAgo": 0,
                "ownerEmail": normalize_email(account.get("email")),
            },
            "balance": {
                "available": decimal_usdc_to_float(
                    account.get("circleUsdcBalance"),
                    fallback=atomic_to_usdc(account.get("availableAtomic")),
                ),
                "locked": atomic_to_usdc(account.get("lockedAtomic")),
                "lifetimeIn": round(lifetime_in, 6),
                "lifetimeOut": round(lifetime_out, 6),
            },
            "transactions": [
                dashboard_transaction(entry, escrow_by_id)
                for entry in agent_entries
            ],
            "settings": {"limits": {"perTradeCap": 0.01}},
        }

    return {
        "agents": agents,
        "defaultAgentId": next(iter(agents), None),
        "source": "ledger",
    }


def scoped_ledger_state(
    ledger_state: dict[str, Any],
    agent_id: Optional[str] = None,
) -> dict[str, Any]:
    scoped_agent_id = str(agent_id or "").strip()
    if not scoped_agent_id:
        state = dict(ledger_state)
        state["accounts"] = []
        state["entries"] = []
        state["escrows"] = []
        state["onrampSessions"] = []
        state["onrampEvents"] = []
        state["chainRecords"] = []
        state["settlementRecords"] = []
        return state

    accounts = [
        account
        for account in ledger_state.get("accounts", [])
        if isinstance(account, dict)
        and str(account.get("agentId") or "").strip() == scoped_agent_id
    ]

    entries = [
        entry
        for entry in ledger_state.get("entries", [])
        if isinstance(entry, dict)
        and str(entry.get("agentId") or "").strip() == scoped_agent_id
    ]
    entry_ids = {
        str(entry.get("entryId") or "").strip()
        for entry in entries
        if str(entry.get("entryId") or "").strip()
    }

    escrows = [
        escrow
        for escrow in ledger_state.get("escrows", [])
        if isinstance(escrow, dict)
        and (
            str(escrow.get("buyerAgentId") or "").strip() == scoped_agent_id
            or str(escrow.get("sellerAgentId") or "").strip() == scoped_agent_id
        )
    ]
    escrow_ids = {
        str(escrow.get("escrowId") or "").strip()
        for escrow in escrows
        if str(escrow.get("escrowId") or "").strip()
    }

    onramp_sessions = [
        session
        for session in ledger_state.get("onrampSessions", [])
        if isinstance(session, dict)
        and str(session.get("agentId") or "").strip() == scoped_agent_id
    ]
    onramp_session_ids = {
        str(session.get("sessionId") or "").strip()
        for session in onramp_sessions
        if str(session.get("sessionId") or "").strip()
    }

    chain_records = [
        record
        for record in ledger_state.get("chainRecords", [])
        if isinstance(record, dict)
        and (
            str(record.get("escrowId") or "").strip() in escrow_ids
            or any(
                str(entry_id or "").strip() in entry_ids
                for entry_id in record.get("entryIds", [])
            )
        )
    ]
    settlement_records = [
        record
        for record in ledger_state.get("settlementRecords", [])
        if isinstance(record, dict)
        and (
            str(record.get("escrowId") or "").strip() in escrow_ids
            or str(record.get("fromAgentId") or "").strip() == scoped_agent_id
            or str(record.get("toAgentId") or "").strip() == scoped_agent_id
        )
    ]

    state = dict(ledger_state)
    state["accounts"] = accounts
    state["entries"] = entries
    state["escrows"] = escrows
    state["onrampSessions"] = onramp_sessions
    state["onrampEvents"] = [
        event
        for event in ledger_state.get("onrampEvents", [])
        if isinstance(event, dict)
        and str(event.get("sessionId") or "").strip() in onramp_session_ids
    ]
    state["chainRecords"] = chain_records
    state["settlementRecords"] = settlement_records
    return state


def build_claimable_agents(
    *,
    email: str,
    ledger_state: dict[str, Any],
    claimed_agent_ids: list[str],
) -> dict[str, Any]:
    normalized_email = normalize_email(email)
    if normalized_email is None:
        raise HTTPException(status_code=400, detail="email is required")
    claimed = {str(agent_id).strip() for agent_id in claimed_agent_ids if str(agent_id).strip()}
    dashboard_state = build_dashboard_data(ledger_state)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    accounts = [
        account
        for account in ledger_state.get("accounts", [])
        if isinstance(account, dict)
    ]
    for account in sorted(accounts, key=lambda item: str(item.get("updatedAt") or ""), reverse=True):
        agent_id = str(account.get("agentId") or "").strip()
        if not agent_id or agent_id in seen or agent_id in claimed:
            continue
        if normalize_email(account.get("email")) != normalized_email:
            continue
        seen.add(agent_id)
        wallet_address = (
            account.get("walletAddress")
            or account.get("circleWalletId")
            or agent_id
        )
        dashboard_agent = dashboard_state["agents"].get(agent_id) or empty_dashboard_agent(account)
        candidates.append(
            {
                "agentId": agent_id,
                "agentName": str(account.get("agentName") or agent_id),
                "ownerEmail": normalized_email,
                "walletAddress": str(wallet_address),
                "displayWalletAddress": short_address(wallet_address),
                "circleWalletId": account.get("circleWalletId"),
                "claimStatus": "unclaimed",
                "dashboard": dashboard_agent,
            }
        )
    return {
        "email": normalized_email,
        "agents": candidates,
        "source": "ledger-accounts",
    }


class CoinbaseAuth:
    def __init__(
        self,
        *,
        bearer_token: Optional[str],
        api_key_id: Optional[str],
        api_private_key: Optional[str],
    ) -> None:
        self.bearer_token = bearer_token
        self.api_key_id = api_key_id
        self.api_private_key = api_private_key

    def bearer_for(self, *, method: str, host: str, path: str) -> str:
        if self.bearer_token:
            return self.bearer_token
        if self.api_key_id and self.api_private_key:
            issued_at = int(time.time())
            payload = {
                "sub": self.api_key_id,
                "iss": "cdp",
                "nbf": issued_at,
                "exp": issued_at + 120,
                "uri": f"{method.upper()} {host}{path}",
            }
            return jwt.encode(
                payload,
                self._ed25519_private_key(),
                algorithm="EdDSA",
                headers={"kid": self.api_key_id, "nonce": secrets.token_hex()},
            )
        if not self.api_key_id or not self.api_private_key:
            raise RuntimeError(
                "Coinbase onramp auth is not configured. Set COINBASE_ONRAMP_BEARER_TOKEN "
                "or COINBASE_API_KEY_ID and COINBASE_API_PRIVATE_KEY."
            )

    def _ed25519_private_key(self) -> ed25519.Ed25519PrivateKey:
        assert self.api_private_key is not None
        try:
            raw = base64.b64decode(self.api_private_key)
        except Exception as error:
            raise RuntimeError("COINBASE_API_PRIVATE_KEY must be base64 encoded") from error
        # Coinbase JSON keys commonly contain either the 32-byte Ed25519 seed or
        # a 64-byte private+public key payload. Cryptography expects the seed.
        if len(raw) == 64:
            raw = raw[:32]
        if len(raw) != 32:
            raise RuntimeError("COINBASE_API_PRIVATE_KEY must decode to 32 or 64 bytes")
        return ed25519.Ed25519PrivateKey.from_private_bytes(raw)


class CoinbaseOnrampClient:
    def __init__(
        self,
        *,
        api_base_url: str,
        token_path: str,
        hosted_url: str,
        auth: CoinbaseAuth,
        mock: bool,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.token_path = token_path
        self.hosted_url = hosted_url
        self.auth = auth
        self.mock = mock

    async def create_session_token(
        self,
        *,
        destination_address: str,
        destination_network: str,
        purchase_currency: str,
        client_ip: str,
    ) -> dict[str, str]:
        if self.mock:
            return {
                "token": f"mock-session-token-{uuid.uuid4().hex}",
                "channel_id": f"mock-channel-{uuid.uuid4().hex}",
            }
        url = f"{self.api_base_url}{self.token_path}"
        parsed = httpx.URL(url)
        bearer = self.auth.bearer_for(
            method="POST",
            host=parsed.host,
            path=parsed.raw_path.decode("ascii"),
        )
        body = {
            "addresses": [
                {
                    "address": destination_address,
                    "blockchains": [destination_network],
                }
            ],
            "assets": [purchase_currency],
            "clientIp": client_ip,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {bearer}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Coinbase onramp token request failed: {response.text}")
        payload = response.json()
        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Coinbase onramp token response did not include token")
        channel_id = payload.get("channel_id")
        return {
            "token": token,
            "channel_id": channel_id if isinstance(channel_id, str) else "",
        }

    def hosted_onramp_url(
        self,
        *,
        session_token: str,
        partner_user_ref: Optional[str],
        redirect_url: Optional[str],
        destination_network: str,
        purchase_currency: str,
        payment_currency: str,
        payment_amount: str,
        default_payment_method: Optional[str],
    ) -> str:
        query = {
            "sessionToken": session_token,
            "defaultNetwork": destination_network,
            "defaultAsset": purchase_currency,
            "fiatCurrency": payment_currency,
            "presetFiatAmount": payment_amount,
        }
        if partner_user_ref:
            query["partnerUserRef"] = partner_user_ref
        if redirect_url:
            query["redirectUrl"] = redirect_url
        if default_payment_method:
            query["defaultPaymentMethod"] = default_payment_method
        return f"{self.hosted_url}?{urlencode(query)}"


class LedgerChainRecorder:
    def __init__(
        self,
        *,
        enabled: bool,
        chain_http_url: str,
        recorder_address: str,
        timeout_seconds: float,
        max_payload_bytes: int,
        require_success: bool,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.enabled = enabled
        self.chain_http_url = chain_http_url
        self.recorder_address = recorder_address
        self.timeout_seconds = timeout_seconds
        self.max_payload_bytes = max_payload_bytes
        self.require_success = require_success
        self.transport = transport

    async def submit(
        self,
        *,
        event_type: Literal[
            "credit",
            "escrow_lock",
            "escrow_release",
            "escrow_refund",
            "agent_transfer",
        ],
        escrow: Optional[EscrowRecord],
        entries: list[LedgerEntry],
        payload: dict[str, Any],
    ) -> Optional[LedgerChainRecord]:
        if not self.enabled:
            return None

        current = now_iso()
        base_record = {
            "recordId": f"chainrec_{uuid.uuid4().hex}",
            "eventType": event_type,
            "chainMcpUrl": self.chain_http_url,
            "recorderAddress": self.recorder_address,
            "escrowId": escrow.escrowId if escrow is not None else None,
            "entryIds": [entry.entryId for entry in entries],
            "payload": payload,
            "createdAt": current,
            "updatedAt": current,
        }

        try:
            data = encode_ledger_record_payload(payload, self.max_payload_bytes)
            structured = await self._call_chain_submit_execution(data)
            error_payload = structured.get("error")
            if isinstance(error_payload, dict):
                message = error_payload.get("message")
                raise RuntimeError(str(message or error_payload))
            execution = structured.get("execution")
            settlement = structured.get("settlement")
            tx_hash = None
            mode = None
            if isinstance(execution, dict):
                tx_hash = execution.get("txHash")
                mode = execution.get("mode")
            if not isinstance(tx_hash, str) and isinstance(settlement, dict):
                identifier = settlement.get("identifier")
                if isinstance(identifier, str) and identifier.startswith("0x"):
                    tx_hash = identifier
                mode = mode or settlement.get("mode")
            if not isinstance(tx_hash, str) or not tx_hash.startswith("0x"):
                raise RuntimeError("chain REST response did not include a transaction hash")
            return LedgerChainRecord(
                **base_record,
                status="submitted",
                txHash=tx_hash,
                mode=mode if isinstance(mode, str) else None,
                toolResult=structured,
            )
        except Exception as error:
            record = LedgerChainRecord(
                **base_record,
                status="failed",
                error=str(error),
            )
            if self.require_success:
                raise LedgerChainRecordError(record) from error
            return record

    async def _call_chain_submit_execution(self, data: str) -> dict[str, Any]:
        client = ChainHttpClient(
            base_url=self.chain_http_url,
            timeout_seconds=self.timeout_seconds,
            transport=self.transport,
        )
        return await client.submit_execution(
            to=self.recorder_address,
            value_eth="0",
            data=data,
        )


class LedgerChainRecordError(RuntimeError):
    def __init__(self, record: LedgerChainRecord) -> None:
        super().__init__(record.error or "ledger chain record failed")
        self.record = record


class LedgerSettlementClient:
    def __init__(
        self,
        *,
        enabled: bool,
        settlement_http_url: str,
        timeout_seconds: float,
        require_success: bool,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.enabled = enabled
        self.settlement_http_url = settlement_http_url
        self.timeout_seconds = timeout_seconds
        self.require_success = require_success
        self.transport = transport

    async def submit_release(self, escrow: EscrowRecord) -> Optional[LedgerSettlementRecord]:
        if not self.enabled:
            return None

        current = now_iso()
        base_record = {
            "recordId": f"settle_{uuid.uuid4().hex}",
            "eventType": "escrow_release",
            "chainMcpUrl": self.settlement_http_url,
            "escrowId": escrow.escrowId,
            "fromAgentId": escrow.buyerAgentId,
            "toAgentId": escrow.sellerAgentId,
            "asset": escrow.asset,
            "amountAtomic": escrow.amountAtomic,
            "createdAt": current,
            "updatedAt": current,
        }
        try:
            result = await self._call_transfer_tool(
                from_agent_id=escrow.buyerAgentId,
                to_agent_id=escrow.sellerAgentId,
                amount_atomic=escrow.amountAtomic,
                ref_id=f"{escrow.escrowId}:release",
            )
            transfer = result
            if transfer.get("error") is not None:
                raise RuntimeError(json.dumps(transfer["error"], sort_keys=True))
            return LedgerSettlementRecord(
                **base_record,
                status="submitted",
                transactionId=transfer.get("transactionId")
                if isinstance(transfer.get("transactionId"), str)
                else None,
                transactionHash=transfer.get("transactionHash")
                if isinstance(transfer.get("transactionHash"), str)
                else None,
                transactionState=transfer.get("state")
                if isinstance(transfer.get("state"), str)
                else None,
                mode=transfer.get("mode") if isinstance(transfer.get("mode"), str) else None,
                toolResult=result,
            )
        except Exception as error:
            record = LedgerSettlementRecord(
                **base_record,
                status="failed",
                error=str(error),
            )
            if self.require_success:
                raise LedgerSettlementError(record) from error
            return record

    async def submit_agent_transfer(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        amount_atomic: str,
        ref_id: str,
    ) -> LedgerSettlementRecord:
        current = now_iso()
        base_record = {
            "recordId": f"settle_{uuid.uuid4().hex}",
            "eventType": "agent_transfer",
            "chainMcpUrl": self.settlement_http_url,
            "transferId": ref_id,
            "fromAgentId": from_agent_id,
            "toAgentId": to_agent_id,
            "asset": DEFAULT_ASSET,
            "amountAtomic": amount_atomic,
            "createdAt": current,
            "updatedAt": current,
        }
        if not self.enabled:
            record = LedgerSettlementRecord(
                **base_record,
                status="failed",
                error="Circle settlement is required for direct agent transfers",
            )
            raise LedgerSettlementError(record)
        try:
            result = await self._call_transfer_tool(
                from_agent_id=from_agent_id,
                to_agent_id=to_agent_id,
                amount_atomic=amount_atomic,
                ref_id=ref_id,
            )
            transfer = result
            if transfer.get("error") is not None:
                raise RuntimeError(json.dumps(transfer["error"], sort_keys=True))
            return LedgerSettlementRecord(
                **base_record,
                status="submitted",
                transactionId=transfer.get("transactionId")
                if isinstance(transfer.get("transactionId"), str)
                else None,
                transactionHash=transfer.get("transactionHash")
                if isinstance(transfer.get("transactionHash"), str)
                else None,
                transactionState=transfer.get("state")
                if isinstance(transfer.get("state"), str)
                else None,
                mode=transfer.get("mode") if isinstance(transfer.get("mode"), str) else None,
                toolResult=result,
            )
        except LedgerSettlementError:
            raise
        except Exception as error:
            record = LedgerSettlementRecord(
                **base_record,
                status="failed",
                error=str(error),
            )
            raise LedgerSettlementError(record) from error

    async def submit_withdrawal(
        self,
        *,
        from_agent_id: str,
        to_address: str,
        amount_atomic: str,
        ref_id: str,
    ) -> LedgerSettlementRecord:
        current = now_iso()
        destination = normalize_evm_address(to_address)
        base_record = {
            "recordId": f"settle_{uuid.uuid4().hex}",
            "eventType": "withdrawal",
            "chainMcpUrl": self.settlement_http_url,
            "transferId": ref_id,
            "fromAgentId": from_agent_id,
            "toAddress": destination,
            "asset": DEFAULT_ASSET,
            "amountAtomic": amount_atomic,
            "createdAt": current,
            "updatedAt": current,
        }
        if not self.enabled:
            record = LedgerSettlementRecord(
                **base_record,
                status="failed",
                error="Circle settlement is required for withdrawals",
            )
            raise LedgerSettlementError(record)
        try:
            result = await self._call_transfer_tool(
                from_agent_id=from_agent_id,
                to_address=destination,
                amount_atomic=amount_atomic,
                ref_id=ref_id,
            )
            transfer = result
            if transfer.get("error") is not None:
                raise RuntimeError(json.dumps(transfer["error"], sort_keys=True))
            return LedgerSettlementRecord(
                **base_record,
                status="submitted",
                transactionId=transfer.get("transactionId")
                if isinstance(transfer.get("transactionId"), str)
                else None,
                transactionHash=transfer.get("transactionHash")
                if isinstance(transfer.get("transactionHash"), str)
                else None,
                transactionState=transfer.get("state")
                if isinstance(transfer.get("state"), str)
                else None,
                mode=transfer.get("mode") if isinstance(transfer.get("mode"), str) else None,
                toolResult=result,
            )
        except LedgerSettlementError:
            raise
        except Exception as error:
            record = LedgerSettlementRecord(
                **base_record,
                status="failed",
                error=str(error),
            )
            raise LedgerSettlementError(record) from error

    async def _call_transfer_tool(
        self,
        *,
        from_agent_id: str,
        to_agent_id: Optional[str] = None,
        to_address: Optional[str] = None,
        amount_atomic: str,
        ref_id: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "fromAgentId": from_agent_id,
            "amountAtomic": amount_atomic,
            "refId": ref_id,
        }
        if to_agent_id is not None:
            payload["toAgentId"] = to_agent_id
        if to_address is not None:
            payload["toAddress"] = to_address
        client = CircleHttpClient(
            base_url=self.settlement_http_url,
            timeout_seconds=self.timeout_seconds,
            transport=self.transport,
        )
        return await client.settle(payload)


class LedgerSettlementError(RuntimeError):
    def __init__(self, record: LedgerSettlementRecord) -> None:
        super().__init__(record.error or "ledger settlement failed")
        self.record = record


class LedgerWalletClient:
    def __init__(
        self,
        *,
        wallet_http_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.wallet_http_url = wallet_http_url
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def get_or_create(self, request: AgentWalletRequest) -> dict[str, Any]:
        client = CircleHttpClient(
            base_url=self.wallet_http_url,
            timeout_seconds=self.timeout_seconds,
            transport=self.transport,
        )
        wallet = await client.get_or_create_wallet(
            {
                "agentName": request.agentName,
                "agentId": request.agentId,
                "email": request.email,
                "walletAddress": request.walletAddress,
                "circleWalletId": request.circleWalletId,
                "agentDescription": request.agentDescription,
            },
        )
        if not wallet:
            raise RuntimeError("wallet REST response did not include wallet content")
        return wallet

    async def status(
        self,
        *,
        wallet_address: Optional[str],
        circle_wallet_id: Optional[str],
    ) -> dict[str, Any]:
        client = CircleHttpClient(
            base_url=self.wallet_http_url,
            timeout_seconds=self.timeout_seconds,
            transport=self.transport,
        )
        wallet = await client.wallet_status(
            wallet_address=wallet_address,
            circle_wallet_id=circle_wallet_id,
        )
        if not wallet:
            raise RuntimeError("wallet REST response did not include wallet status content")
        return wallet

    async def gateway_deposit(self, request: GatewayDepositRequest) -> dict[str, Any]:
        client = CircleHttpClient(
            base_url=self.wallet_http_url,
            timeout_seconds=self.timeout_seconds,
            transport=self.transport,
        )
        deposit = await client.gateway_deposit(
            {
                "agentId": request.agentId,
                "amountAtomic": request.amountAtomic,
                "refId": request.refId,
            },
        )
        if not deposit:
            raise RuntimeError("wallet REST response did not include Gateway deposit content")
        return deposit

    async def gateway_withdraw(self, request: GatewayWithdrawalRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "agentId": request.agentId,
            "amountAtomic": request.amountAtomic,
            "refId": request.refId,
        }
        if request.recipientAddress is not None:
            payload["recipientAddress"] = request.recipientAddress
        client = CircleHttpClient(
            base_url=self.wallet_http_url,
            timeout_seconds=self.timeout_seconds,
            transport=self.transport,
        )
        withdrawal = await client.gateway_withdraw(payload)
        if not withdrawal:
            raise RuntimeError("wallet REST response did not include Gateway withdrawal content")
        return withdrawal


def encode_ledger_record_payload(payload: dict[str, Any], max_payload_bytes: int) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    if len(raw) > max_payload_bytes:
        raise ValueError(
            f"ledger chain record payload is {len(raw)} bytes; max is {max_payload_bytes}"
        )
    return "0x" + raw.hex()


class OffchainLedgerStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()

    def load(self) -> LedgerState:
        with self._lock:
            return self._load_unlocked()

    def ensure_account(self, agent_id: str) -> LedgerAccount:
        def mutate(state: LedgerState) -> LedgerAccount:
            account, _ = self._account_for_update(state, agent_id, create=True)
            return account

        return self._mutate(mutate)

    def bind_account_wallet(
        self,
        *,
        agent_id: str,
        agent_name: Optional[str] = None,
        email: Optional[str] = None,
        wallet_address: Optional[str],
        circle_wallet_id: Optional[str],
    ) -> LedgerAccount:
        def mutate(state: LedgerState) -> LedgerAccount:
            account, account_index = self._account_for_update(
                state, agent_id, create=True
            )
            updates: dict[str, Any] = {"updatedAt": now_iso()}
            if agent_name is not None:
                updates["agentName"] = agent_name
            if email is not None:
                updates["email"] = email
            if wallet_address is not None:
                updates["walletAddress"] = wallet_address
            if circle_wallet_id is not None:
                updates["circleWalletId"] = circle_wallet_id
            updated = account.model_copy(update=updates)
            state.accounts[account_index] = updated
            return updated

        return self._mutate(mutate)

    def credit(
        self,
        *,
        agent_id: str,
        amount_atomic: str,
        reason: Optional[str],
        metadata: dict[str, Any],
    ) -> tuple[LedgerAccount, LedgerEntry]:
        amount = parse_positive_atomic(amount_atomic)

        def mutate(state: LedgerState) -> tuple[LedgerAccount, LedgerEntry]:
            account, account_index = self._account_for_update(state, agent_id, create=True)
            current = now_iso()
            updated = account.model_copy(
                update={
                    "availableAtomic": add_atomic(account.availableAtomic, amount),
                    "updatedAt": current,
                }
            )
            state.accounts[account_index] = updated
            entry = self._entry(
                entry_type="credit",
                agent_id=agent_id,
                available_delta=amount,
                reason=reason,
                metadata=metadata,
            )
            state.entries.append(entry)
            return updated, entry

        return self._mutate(mutate)

    def validate_agent_transfer(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        amount_atomic: str,
    ) -> None:
        parse_positive_atomic(amount_atomic)
        state = self.load()
        sender = self._find_account(state, from_agent_id)
        receiver = self._find_account(state, to_agent_id)
        if sender is None:
            raise ValueError("sender account not found")
        if receiver is None:
            raise ValueError("receiver account not found")
        self._require_circle_wallet(sender, "sender")
        self._require_circle_wallet(receiver, "receiver")

    def validate_withdrawal(
        self,
        *,
        agent_id: str,
        amount_atomic: str,
        owner_email: Optional[str],
        available_atomic: Optional[str] = None,
    ) -> LedgerAccount:
        amount = parse_positive_atomic(amount_atomic)
        state = self.load()
        account = self._find_account(state, agent_id)
        if account is None:
            raise ValueError("agent account not found")
        self._require_circle_wallet(account, "source")
        normalized_owner_email = normalize_email(owner_email)
        if normalized_owner_email and normalize_email(account.email) != normalized_owner_email:
            raise ValueError("ownerEmail does not match agent account")
        balance_basis = (
            parse_nonnegative_atomic(available_atomic)
            if available_atomic is not None
            else parse_nonnegative_atomic(account.availableAtomic)
        )
        if balance_basis < amount:
            raise ValueError("amount exceeds available balance")
        return account

    def account_by_email(self, email: str) -> LedgerAccount:
        normalized = normalize_email(email)
        if normalized is None:
            raise ValueError("email must not be empty")
        state = self.load()
        for account in state.accounts:
            if normalize_email(account.email) == normalized:
                return account
        raise LookupError(f"ledger account email not found: {normalized}")

    def transfer_between_agents(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        amount_atomic: str,
        reason: Optional[str],
        metadata: dict[str, Any],
        transfer_id: str,
        settlement_record_id: Optional[str],
    ) -> tuple[LedgerAccount, LedgerAccount, list[LedgerEntry]]:
        amount = parse_positive_atomic(amount_atomic)

        def mutate(state: LedgerState) -> tuple[LedgerAccount, LedgerAccount, list[LedgerEntry]]:
            sender, _sender_index = self._account_for_update(
                state, from_agent_id, create=False
            )
            receiver, _receiver_index = self._account_for_update(
                state, to_agent_id, create=False
            )
            self._require_circle_wallet(sender, "sender")
            self._require_circle_wallet(receiver, "receiver")

            entry_metadata = {
                **metadata,
                "transferId": transfer_id,
            }
            if settlement_record_id is not None:
                entry_metadata["settlementRecordId"] = settlement_record_id
            sender_entry = self._entry(
                entry_type="agent_transfer",
                agent_id=from_agent_id,
                available_delta=-amount,
                reason=reason or "agent transfer sent",
                metadata={**entry_metadata, "counterpartyAgentId": to_agent_id},
            )
            receiver_entry = self._entry(
                entry_type="agent_transfer",
                agent_id=to_agent_id,
                available_delta=amount,
                reason=reason or "agent transfer received",
                metadata={**entry_metadata, "counterpartyAgentId": from_agent_id},
            )
            state.entries.extend([sender_entry, receiver_entry])
            return sender, receiver, [sender_entry, receiver_entry]

        return self._mutate(mutate)

    def withdraw(
        self,
        *,
        agent_id: str,
        destination_address: str,
        amount_atomic: str,
        reason: Optional[str],
        metadata: dict[str, Any],
        withdrawal_id: str,
        settlement_record_id: Optional[str],
        available_atomic: Optional[str] = None,
    ) -> tuple[LedgerAccount, LedgerEntry]:
        amount = parse_positive_atomic(amount_atomic)
        destination = normalize_evm_address(destination_address)

        def mutate(state: LedgerState) -> tuple[LedgerAccount, LedgerEntry]:
            account, account_index = self._account_for_update(
                state, agent_id, create=False
            )
            self._require_circle_wallet(account, "source")
            balance_basis = (
                parse_nonnegative_atomic(available_atomic)
                if available_atomic is not None
                else parse_nonnegative_atomic(account.availableAtomic)
            )
            if balance_basis < amount:
                raise ValueError("amount exceeds available balance")
            current = now_iso()
            updated = account.model_copy(
                update={
                    "availableAtomic": str(balance_basis - amount),
                    "updatedAt": current,
                }
            )
            entry_metadata = {
                **metadata,
                "withdrawalId": withdrawal_id,
                "destinationAddress": destination,
                "counterparty": f"External · {short_address(destination)}",
            }
            if settlement_record_id is not None:
                entry_metadata["settlementRecordId"] = settlement_record_id
            entry = self._entry(
                entry_type="withdrawal",
                agent_id=agent_id,
                available_delta=-amount,
                reason=reason or "withdrawal",
                metadata=entry_metadata,
            )
            state.accounts[account_index] = updated
            state.entries.append(entry)
            return updated, entry

        return self._mutate(mutate)

    def create_escrow(
        self,
        *,
        buyer_agent_id: str,
        seller_agent_id: str,
        amount_atomic: str,
        task_id: Optional[str],
        description: Optional[str],
        metadata: dict[str, Any],
    ) -> tuple[EscrowRecord, LedgerEntry]:
        amount = parse_positive_atomic(amount_atomic)

        def mutate(state: LedgerState) -> tuple[EscrowRecord, LedgerEntry]:
            buyer, buyer_index = self._account_for_update(
                state, buyer_agent_id, create=False
            )
            if int(buyer.availableAtomic) < amount:
                raise ValueError("insufficient available balance")

            current = now_iso()
            updated_buyer = buyer.model_copy(
                update={
                    "availableAtomic": add_atomic(buyer.availableAtomic, -amount),
                    "lockedAtomic": add_atomic(buyer.lockedAtomic, amount),
                    "updatedAt": current,
                }
            )
            escrow = EscrowRecord(
                escrowId=f"escrow_{uuid.uuid4().hex}",
                buyerAgentId=buyer_agent_id,
                sellerAgentId=seller_agent_id,
                amountAtomic=str(amount),
                status="locked",
                taskId=task_id,
                description=description,
                createdAt=current,
                updatedAt=current,
            )
            entry = self._entry(
                entry_type="escrow_lock",
                agent_id=buyer_agent_id,
                available_delta=-amount,
                locked_delta=amount,
                escrow_id=escrow.escrowId,
                reason="escrow created",
                metadata=metadata,
            )
            state.accounts[buyer_index] = updated_buyer
            state.escrows.append(escrow)
            state.entries.append(entry)
            return escrow, entry

        return self._mutate(mutate)

    def release_escrow(self, escrow_id: str) -> EscrowRecord:
        def mutate(state: LedgerState) -> EscrowRecord:
            escrow, escrow_index = self._escrow_for_update(state, escrow_id)
            self._require_locked(escrow)
            amount = int(escrow.amountAtomic)
            buyer, buyer_index = self._account_for_update(
                state, escrow.buyerAgentId, create=False
            )
            seller, seller_index = self._account_for_update(
                state, escrow.sellerAgentId, create=True
            )
            current = now_iso()
            state.accounts[buyer_index] = buyer.model_copy(
                update={
                    "lockedAtomic": add_atomic(buyer.lockedAtomic, -amount),
                    "updatedAt": current,
                }
            )
            state.accounts[seller_index] = seller.model_copy(
                update={
                    "availableAtomic": add_atomic(seller.availableAtomic, amount),
                    "updatedAt": current,
                }
            )
            updated_escrow = escrow.model_copy(
                update={
                    "status": "released",
                    "releasedAt": current,
                    "updatedAt": current,
                }
            )
            state.escrows[escrow_index] = updated_escrow
            state.entries.append(
                self._entry(
                    entry_type="escrow_release",
                    agent_id=escrow.buyerAgentId,
                    locked_delta=-amount,
                    escrow_id=escrow.escrowId,
                    reason="escrow released",
                )
            )
            state.entries.append(
                self._entry(
                    entry_type="escrow_release",
                    agent_id=escrow.sellerAgentId,
                    available_delta=amount,
                    escrow_id=escrow.escrowId,
                    reason="escrow released",
                )
            )
            return updated_escrow

        return self._mutate(mutate)

    def refund_escrow(self, escrow_id: str) -> EscrowRecord:
        def mutate(state: LedgerState) -> EscrowRecord:
            escrow, escrow_index = self._escrow_for_update(state, escrow_id)
            self._require_locked(escrow)
            amount = int(escrow.amountAtomic)
            buyer, buyer_index = self._account_for_update(
                state, escrow.buyerAgentId, create=False
            )
            current = now_iso()
            state.accounts[buyer_index] = buyer.model_copy(
                update={
                    "availableAtomic": add_atomic(buyer.availableAtomic, amount),
                    "lockedAtomic": add_atomic(buyer.lockedAtomic, -amount),
                    "updatedAt": current,
                }
            )
            updated_escrow = escrow.model_copy(
                update={
                    "status": "refunded",
                    "refundedAt": current,
                    "updatedAt": current,
                }
            )
            state.escrows[escrow_index] = updated_escrow
            state.entries.append(
                self._entry(
                    entry_type="escrow_refund",
                    agent_id=escrow.buyerAgentId,
                    available_delta=amount,
                    locked_delta=-amount,
                    escrow_id=escrow.escrowId,
                    reason="escrow refunded",
                )
            )
            return updated_escrow

        return self._mutate(mutate)

    def entries_for_escrow_event(
        self,
        *,
        escrow_id: str,
        entry_type: Literal["escrow_lock", "escrow_release", "escrow_refund"],
    ) -> list[LedgerEntry]:
        state = self.load()
        return [
            entry
            for entry in state.entries
            if entry.escrowId == escrow_id and entry.entryType == entry_type
        ]

    def add_chain_record(self, record: LedgerChainRecord) -> LedgerChainRecord:
        def mutate(state: LedgerState) -> LedgerChainRecord:
            state.chainRecords.append(record)
            return record

        return self._mutate(mutate)

    def add_settlement_record(self, record: LedgerSettlementRecord) -> LedgerSettlementRecord:
        def mutate(state: LedgerState) -> LedgerSettlementRecord:
            state.settlementRecords.append(record)
            return record

        return self._mutate(mutate)

    def get_escrow(self, escrow_id: str) -> EscrowRecord:
        state = self.load()
        for escrow in state.escrows:
            if escrow.escrowId == escrow_id:
                return escrow
        raise LookupError("escrow not found")

    def find_onramp_session_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> Optional[OnrampSessionRecord]:
        state = self.load()
        for session in state.onrampSessions:
            if session.idempotencyKey == idempotency_key:
                return session
        return None

    def get_onramp_session(self, session_id: str) -> OnrampSessionRecord:
        state = self.load()
        for session in state.onrampSessions:
            if session.sessionId == session_id:
                return session
        raise LookupError("onramp session not found")

    def add_onramp_session(self, session: OnrampSessionRecord) -> OnrampSessionRecord:
        def mutate(state: LedgerState) -> OnrampSessionRecord:
            for existing in state.onrampSessions:
                if existing.idempotencyKey == session.idempotencyKey:
                    return existing
            state.onrampSessions.append(session)
            state.onrampEvents.append(
                OnrampEventRecord(
                    eventId=f"evt_{uuid.uuid4().hex}",
                    sessionId=session.sessionId,
                    eventType="session_created",
                    rawPayload={"idempotencyKey": session.idempotencyKey},
                    createdAt=session.createdAt,
                )
            )
            return session

        return self._mutate(mutate)

    def confirm_onramp_session(
        self,
        session_id: str,
        request: ConfirmOnrampSessionRequest,
    ) -> OnrampSessionRecord:
        amount = parse_positive_atomic(request.amountAtomic)

        def mutate(state: LedgerState) -> OnrampSessionRecord:
            session, session_index = self._onramp_session_for_update(state, session_id)
            if session.status == "credited":
                return session

            metadata = {
                "onrampSessionId": session.sessionId,
                "provider": "coinbase",
                "providerOrderId": request.providerOrderId,
                "destinationAddress": session.destinationAddress,
                "destinationNetwork": session.destinationNetwork,
                "asset": session.purchaseCurrency,
            }
            if request.txHash:
                metadata["txHash"] = request.txHash

            account, account_index = self._account_for_update(
                state, session.agentId, create=True
            )
            current = now_iso()
            updated_account = account.model_copy(
                update={
                    "availableAtomic": add_atomic(account.availableAtomic, amount),
                    "updatedAt": current,
                }
            )
            entry = self._entry(
                entry_type="credit",
                agent_id=session.agentId,
                available_delta=amount,
                reason="coinbase_onramp_confirmed",
                metadata=metadata,
            )
            updated_session = session.model_copy(
                update={
                    "status": "credited",
                    "providerOrderId": request.providerOrderId,
                    "creditedAmountAtomic": str(amount),
                    "txHash": request.txHash,
                    "ledgerEntryId": entry.entryId,
                    "creditedAt": current,
                    "updatedAt": current,
                }
            )
            state.accounts[account_index] = updated_account
            state.entries.append(entry)
            state.onrampSessions[session_index] = updated_session
            state.onrampEvents.append(
                OnrampEventRecord(
                    eventId=f"evt_{uuid.uuid4().hex}",
                    sessionId=session.sessionId,
                    eventType="ledger_credited",
                    providerEventId=request.providerEventId,
                    rawPayload=request.rawPayload,
                    createdAt=current,
                )
            )
            return updated_session

        return self._mutate(mutate)

    def _load_unlocked(self) -> LedgerState:
        if not os.path.exists(self.path):
            return LedgerState()
        with open(self.path, encoding="utf-8") as handle:
            return LedgerState.model_validate(json.load(handle))

    def _save_unlocked(self, state: LedgerState) -> None:
        parent_dir = os.path.dirname(self.path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        target_dir = parent_dir or "."
        fd, temp_path = tempfile.mkstemp(
            prefix=".offchain-ledger-",
            suffix=".tmp",
            dir=target_dir,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state.model_dump(), handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except Exception:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
            raise

    def _mutate(self, mutator):
        with self._lock:
            state = self._load_unlocked()
            result = mutator(state)
            self._save_unlocked(state)
            return result

    def _account_for_update(
        self, state: LedgerState, agent_id: str, *, create: bool
    ) -> tuple[LedgerAccount, int]:
        for index, account in enumerate(state.accounts):
            if account.agentId == agent_id and account.asset == DEFAULT_ASSET:
                return account, index
        if not create:
            raise ValueError("account not found")
        account = LedgerAccount(
            agentId=agent_id,
            createdAt=now_iso(),
            updatedAt=now_iso(),
        )
        state.accounts.append(account)
        return account, len(state.accounts) - 1

    @staticmethod
    def _find_account(state: LedgerState, agent_id: str) -> Optional[LedgerAccount]:
        for account in state.accounts:
            if account.agentId == agent_id and account.asset == DEFAULT_ASSET:
                return account
        return None

    @staticmethod
    def _require_circle_wallet(account: LedgerAccount, role: str) -> None:
        if not account.circleWalletId and not account.walletAddress:
            raise ValueError(f"{role} account is not bound to a Circle wallet")

    def _escrow_for_update(
        self, state: LedgerState, escrow_id: str
    ) -> tuple[EscrowRecord, int]:
        for index, escrow in enumerate(state.escrows):
            if escrow.escrowId == escrow_id:
                return escrow, index
        raise LookupError("escrow not found")

    def _onramp_session_for_update(
        self, state: LedgerState, session_id: str
    ) -> tuple[OnrampSessionRecord, int]:
        for index, session in enumerate(state.onrampSessions):
            if session.sessionId == session_id:
                return session, index
        raise LookupError("onramp session not found")

    @staticmethod
    def _require_locked(escrow: EscrowRecord) -> None:
        if escrow.status != "locked":
            raise ValueError("escrow is not locked")

    @staticmethod
    def _entry(
        *,
        entry_type: Literal[
            "credit",
            "escrow_lock",
            "escrow_release",
            "escrow_refund",
            "agent_transfer",
        ],
        agent_id: str,
        available_delta: int = 0,
        locked_delta: int = 0,
        escrow_id: Optional[str] = None,
        reason: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> LedgerEntry:
        return LedgerEntry(
            entryId=f"entry_{uuid.uuid4().hex}",
            entryType=entry_type,
            agentId=agent_id,
            availableDeltaAtomic=str(available_delta),
            lockedDeltaAtomic=str(locked_delta),
            escrowId=escrow_id,
            reason=reason,
            metadata=metadata or {},
            createdAt=now_iso(),
        )


@lru_cache(maxsize=1)
def get_store() -> OffchainLedgerStore:
    return OffchainLedgerStore(
        os.getenv("LEDGER_STATE_PATH", DEFAULT_LEDGER_STATE_PATH)
    )


@lru_cache(maxsize=1)
def get_coinbase_onramp_client() -> CoinbaseOnrampClient:
    return CoinbaseOnrampClient(
        api_base_url=os.getenv("COINBASE_ONRAMP_API_BASE_URL", DEFAULT_COINBASE_API_BASE_URL),
        token_path=os.getenv("COINBASE_ONRAMP_TOKEN_PATH", DEFAULT_COINBASE_TOKEN_PATH),
        hosted_url=os.getenv("COINBASE_ONRAMP_HOSTED_URL", DEFAULT_COINBASE_HOSTED_URL),
        auth=CoinbaseAuth(
            bearer_token=os.getenv("COINBASE_ONRAMP_BEARER_TOKEN"),
            api_key_id=os.getenv("COINBASE_API_KEY_ID"),
            api_private_key=os.getenv("COINBASE_API_PRIVATE_KEY"),
        ),
        mock=os.getenv("COINBASE_ONRAMP_MOCK", "false").lower() == "true",
    )


@lru_cache(maxsize=1)
def get_chain_recorder() -> LedgerChainRecorder:
    return LedgerChainRecorder(
        enabled=os.getenv("LEDGER_CHAIN_RECORD_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"},
        chain_http_url=os.getenv("LEDGER_CHAIN_HTTP_URL", DEFAULT_CHAIN_HTTP_URL),
        recorder_address=os.getenv("LEDGER_CHAIN_RECORDER_ADDRESS", DEFAULT_CHAIN_RECORDER_ADDRESS),
        timeout_seconds=float(os.getenv("LEDGER_CHAIN_RECORD_TIMEOUT_SECONDS", "30")),
        max_payload_bytes=int(os.getenv("LEDGER_CHAIN_RECORD_MAX_BYTES", "2048")),
        require_success=os.getenv("LEDGER_CHAIN_RECORD_REQUIRE_SUCCESS", "false").lower()
        in {"1", "true", "yes", "on"},
    )


@lru_cache(maxsize=1)
def get_ledger_settlement_client() -> LedgerSettlementClient:
    return LedgerSettlementClient(
        enabled=os.getenv("LEDGER_SETTLEMENT_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"},
        settlement_http_url=os.getenv("LEDGER_SETTLEMENT_HTTP_URL", DEFAULT_SETTLEMENT_HTTP_URL),
        timeout_seconds=float(os.getenv("LEDGER_SETTLEMENT_TIMEOUT_SECONDS", "60")),
        require_success=os.getenv("LEDGER_SETTLEMENT_REQUIRE_SUCCESS", "false").lower()
        in {"1", "true", "yes", "on"},
    )


@lru_cache(maxsize=1)
def get_ledger_wallet_client() -> LedgerWalletClient:
    return LedgerWalletClient(
        wallet_http_url=os.getenv("LEDGER_WALLET_HTTP_URL", DEFAULT_WALLET_HTTP_URL),
        timeout_seconds=float(os.getenv("LEDGER_WALLET_TIMEOUT_SECONDS", "60")),
    )


def ledger_chain_payload(
    *,
    event_type: Literal[
        "credit",
        "escrow_lock",
        "escrow_release",
        "escrow_refund",
        "agent_transfer",
        "withdrawal",
    ],
    escrow: Optional[EscrowRecord],
    entries: list[LedgerEntry],
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "chief-ledger-event",
        "eventType": event_type,
        "entryIds": [entry.entryId for entry in entries],
        "entries": [
            {
                "entryId": entry.entryId,
                "entryType": entry.entryType,
                "agentId": entry.agentId,
                "asset": entry.asset,
                "availableDeltaAtomic": entry.availableDeltaAtomic,
                "lockedDeltaAtomic": entry.lockedDeltaAtomic,
                "escrowId": entry.escrowId,
                "reason": entry.reason,
                "createdAt": entry.createdAt,
            }
            for entry in entries
        ],
        "createdAt": now_iso(),
    }
    if escrow is not None:
        payload["escrow"] = {
            "escrowId": escrow.escrowId,
            "buyerAgentId": escrow.buyerAgentId,
            "sellerAgentId": escrow.sellerAgentId,
            "amountAtomic": escrow.amountAtomic,
            "asset": escrow.asset,
            "status": escrow.status,
            "taskId": escrow.taskId,
            "description": escrow.description,
            "createdAt": escrow.createdAt,
            "updatedAt": escrow.updatedAt,
            "releasedAt": escrow.releasedAt,
            "refundedAt": escrow.refundedAt,
        }
    if extra:
        payload["extra"] = extra
    return payload


async def record_ledger_chain_event(
    *,
    event_type: Literal[
        "credit",
        "escrow_lock",
        "escrow_release",
        "escrow_refund",
        "agent_transfer",
        "withdrawal",
    ],
    escrow: Optional[EscrowRecord],
    entries: list[LedgerEntry],
    extra: Optional[dict[str, Any]] = None,
) -> Optional[LedgerChainRecord]:
    recorder = get_chain_recorder()
    record = await recorder.submit(
        event_type=event_type,
        escrow=escrow,
        entries=entries,
        payload=ledger_chain_payload(
            event_type=event_type,
            escrow=escrow,
            entries=entries,
            extra=extra,
        ),
    )
    if record is None:
        return None
    return get_store().add_chain_record(record)


async def settle_escrow_release(escrow: EscrowRecord) -> Optional[LedgerSettlementRecord]:
    record = await get_ledger_settlement_client().submit_release(escrow)
    if record is None:
        return None
    return get_store().add_settlement_record(record)


async def settle_agent_transfer(
    *,
    from_agent_id: str,
    to_agent_id: str,
    amount_atomic: str,
    ref_id: str,
) -> LedgerSettlementRecord:
    try:
        record = await get_ledger_settlement_client().submit_agent_transfer(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            amount_atomic=amount_atomic,
            ref_id=ref_id,
        )
    except LedgerSettlementError as error:
        get_store().add_settlement_record(error.record)
        logger.error(
            "agent_transfer_settlement_failed %s",
            json.dumps(
                {
                    "transferId": ref_id,
                    "fromAgentId": from_agent_id,
                    "toAgentId": to_agent_id,
                    "amountAtomic": amount_atomic,
                    "settlementRecordId": error.record.recordId,
                    "settlementError": error.record.error,
                },
                sort_keys=True,
            ),
        )
        raise
    logger.info(
        "agent_transfer_settlement_submitted %s",
        json.dumps(
            {
                "transferId": ref_id,
                "fromAgentId": from_agent_id,
                "toAgentId": to_agent_id,
                "amountAtomic": amount_atomic,
                "settlementRecordId": record.recordId,
                "transactionId": record.transactionId,
                "transactionState": record.transactionState,
            },
            sort_keys=True,
        ),
    )
    return get_store().add_settlement_record(record)


async def settle_withdrawal(
    *,
    from_agent_id: str,
    to_address: str,
    amount_atomic: str,
    ref_id: str,
) -> LedgerSettlementRecord:
    try:
        record = await get_ledger_settlement_client().submit_withdrawal(
            from_agent_id=from_agent_id,
            to_address=to_address,
            amount_atomic=amount_atomic,
            ref_id=ref_id,
        )
    except LedgerSettlementError as error:
        get_store().add_settlement_record(error.record)
        logger.error(
            "withdrawal_settlement_failed %s",
            json.dumps(
                {
                    "withdrawalId": ref_id,
                    "fromAgentId": from_agent_id,
                    "toAddress": to_address,
                    "amountAtomic": amount_atomic,
                    "settlementRecordId": error.record.recordId,
                    "settlementError": error.record.error,
                },
                sort_keys=True,
            ),
        )
        raise
    logger.info(
        "withdrawal_settlement_submitted %s",
        json.dumps(
            {
                "withdrawalId": ref_id,
                "fromAgentId": from_agent_id,
                "toAddress": to_address,
                "amountAtomic": amount_atomic,
                "settlementRecordId": record.recordId,
                "transactionId": record.transactionId,
                "transactionState": record.transactionState,
            },
            sort_keys=True,
        ),
    )
    return get_store().add_settlement_record(record)


async def get_or_create_agent_wallet(request: AgentWalletRequest) -> dict[str, Any]:
    wallet = await get_ledger_wallet_client().get_or_create(request)
    binding = wallet.get("binding")
    binding_agent_id = binding.get("agentId") if isinstance(binding, dict) else None
    if binding_agent_id is not None and binding_agent_id != request.agentId:
        raise ValueError("circle wallet binding agentId mismatch")

    wallet_address = wallet.get("walletAddress")
    circle_wallet_id = wallet.get("circleWalletId")
    if isinstance(binding, dict):
        wallet_address = wallet_address or binding.get("walletAddress")
        circle_wallet_id = circle_wallet_id or binding.get("circleWalletId")
    wallet_address = (
        wallet_address if isinstance(wallet_address, str) and wallet_address else None
    )
    circle_wallet_id = (
        circle_wallet_id if isinstance(circle_wallet_id, str) and circle_wallet_id else None
    )
    if wallet_address is not None or circle_wallet_id is not None:
        account = get_store().bind_account_wallet(
            agent_id=request.agentId,
            agent_name=request.agentName,
            email=request.email,
            wallet_address=wallet_address,
            circle_wallet_id=circle_wallet_id,
        )
    else:
        account = get_store().ensure_account(request.agentId)
    return {
        "wallet": wallet,
        "account": account.model_dump(),
    }


def http_error(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, LedgerChainRecordError):
        return HTTPException(
            status_code=502,
            detail={
                "message": str(error),
                "chainRecord": error.record.model_dump(),
            },
        )
    if isinstance(error, LedgerSettlementError):
        return HTTPException(
            status_code=424,
            detail={
                "message": str(error),
                "settlementRecord": error.record.model_dump(),
            },
        )
    return HTTPException(status_code=400, detail=str(error))


@app.get("/health")
def health() -> dict[str, Any]:
    return {"service": "chief-ledger", "status": "ok"}


@app.get("/")
def ledger_home() -> RedirectResponse:
    return RedirectResponse("/dashboard", status_code=307)


@app.get("/admin")
def ledger_admin() -> FileResponse:
    return FileResponse(
        LEDGER_CONSOLE_PATH,
        media_type="text/html",
        headers=NO_CACHE_HEADERS,
    )


@app.get("/admin/ledger/state")
async def get_admin_ledger_state() -> dict[str, Any]:
    return await ledger_state_with_circle_balances()


@app.get("/dashboard")
def ledger_dashboard() -> FileResponse:
    return FileResponse(
        LEDGER_DASHBOARD_PATH,
        media_type="text/html",
        headers=NO_CACHE_HEADERS,
    )


@app.get("/ledger/state")
async def get_ledger_state(agentId: str = "") -> dict[str, Any]:
    return scoped_ledger_state(
        await ledger_state_with_circle_balances(),
        agent_id=agentId,
    )


@app.post("/ledger/payment/route")
def route_ledger_payment_intent(intent: PaymentIntent) -> dict[str, Any]:
    return route_payment_intent(intent)


@app.get("/dashboard/data")
async def dashboard_data(email: str = "") -> dict[str, Any]:
    return build_dashboard_data(
        await ledger_state_with_circle_balances(),
        owner_email=email,
    )


@app.get("/dashboard/claimable-agents")
async def dashboard_claimable_agents(email: str, claimed: str = "") -> dict[str, Any]:
    claimed_agent_ids = [
        item.strip()
        for item in claimed.split(",")
        if item.strip()
    ]
    return build_claimable_agents(
        email=email,
        ledger_state=await ledger_state_with_circle_balances(),
        claimed_agent_ids=claimed_agent_ids,
    )


async def ledger_state_with_circle_balances() -> dict[str, Any]:
    state = get_store().load().model_dump()
    accounts = state.get("accounts")
    if not isinstance(accounts, list):
        return state

    wallet_client = get_ledger_wallet_client()
    for account in accounts:
        if not isinstance(account, dict):
            continue
        wallet_address = account.get("walletAddress")
        circle_wallet_id = account.get("circleWalletId")
        if not isinstance(wallet_address, str) and not isinstance(circle_wallet_id, str):
            continue
        try:
            status = await wallet_client.status(
                wallet_address=wallet_address if isinstance(wallet_address, str) else None,
                circle_wallet_id=circle_wallet_id if isinstance(circle_wallet_id, str) else None,
            )
        except Exception as error:
            account["circleBalanceError"] = str(error)
            continue
        balances = status.get("balances")
        if isinstance(balances, dict):
            usdc_balance = balances.get(DEFAULT_ASSET)
            if isinstance(usdc_balance, str):
                account["circleUsdcBalance"] = usdc_balance
                circle_available_atomic = decimal_usdc_to_atomic_string(usdc_balance)
                if circle_available_atomic is not None:
                    account["availableAtomic"] = circle_available_atomic
                    account["balanceSource"] = "circle"
    return state


@app.post("/onramp/sessions")
async def create_onramp_session(request: CreateOnrampSessionRequest) -> dict[str, Any]:
    store = get_store()
    existing = store.find_onramp_session_by_idempotency_key(request.idempotencyKey)
    if existing is not None:
        return existing.model_dump()

    try:
        coinbase = get_coinbase_onramp_client()
        token_payload = await coinbase.create_session_token(
            destination_address=request.destinationAddress,
            destination_network=request.destinationNetwork,
            purchase_currency=request.purchaseCurrency,
            client_ip=request.clientIp,
        )
    except Exception as error:
        raise http_error(error) from error

    session_id = f"onramp_{uuid.uuid4().hex}"
    partner_user_ref = request.partnerUserRef or session_id
    onramp_url = coinbase.hosted_onramp_url(
        session_token=token_payload["token"],
        partner_user_ref=partner_user_ref,
        redirect_url=str(request.redirectUrl) if request.redirectUrl else None,
        destination_network=request.destinationNetwork,
        purchase_currency=request.purchaseCurrency,
        payment_currency=request.paymentCurrency,
        payment_amount=request.paymentAmount,
        default_payment_method=request.defaultPaymentMethod,
    )
    current = now_iso()
    session = OnrampSessionRecord(
        sessionId=session_id,
        providerToken=token_payload["token"],
        providerChannelId=token_payload.get("channel_id") or None,
        agentId=request.agentId,
        destinationAddress=request.destinationAddress,
        destinationNetwork=request.destinationNetwork,
        purchaseCurrency=request.purchaseCurrency,
        paymentCurrency=request.paymentCurrency,
        paymentAmount=request.paymentAmount,
        clientIp=request.clientIp,
        partnerUserRef=partner_user_ref,
        redirectUrl=str(request.redirectUrl) if request.redirectUrl else None,
        defaultPaymentMethod=request.defaultPaymentMethod,
        idempotencyKey=request.idempotencyKey,
        onrampUrl=onramp_url,
        createdAt=current,
        updatedAt=current,
    )
    return store.add_onramp_session(session).model_dump()


@app.get("/onramp/sessions/{session_id}")
def get_onramp_session(session_id: str) -> dict[str, Any]:
    try:
        return get_store().get_onramp_session(session_id).model_dump()
    except LookupError as error:
        raise http_error(error) from error


@app.post("/onramp/sessions/{session_id}/confirm")
def confirm_onramp_session(
    session_id: str,
    request: ConfirmOnrampSessionRequest,
) -> dict[str, Any]:
    try:
        return get_store().confirm_onramp_session(session_id, request).model_dump()
    except (LookupError, ValueError) as error:
        raise http_error(error) from error


@app.post("/ledger/accounts/{agent_id}/credit")
async def credit_agent_balance(agent_id: str, request: CreditRequest) -> dict[str, Any]:
    try:
        account, entry = get_store().credit(
            agent_id=agent_id,
            amount_atomic=request.amountAtomic,
            reason=request.reason,
            metadata=request.metadata,
        )
        chain_record = await record_ledger_chain_event(
            event_type="credit",
            escrow=None,
            entries=[entry],
            extra={"agentId": agent_id, "amountAtomic": request.amountAtomic},
        )
    except (LookupError, ValueError, LedgerChainRecordError) as error:
        raise http_error(error) from error
    return {
        "account": account.model_dump(),
        "entry": entry.model_dump(),
        "chainRecord": chain_record.model_dump() if chain_record is not None else None,
    }


@app.post("/ledger/wallets/get-or-create")
async def create_or_reuse_agent_wallet(request: AgentWalletRequest) -> dict[str, Any]:
    try:
        return await get_or_create_agent_wallet(request)
    except (ValueError, RuntimeError) as error:
        raise http_error(error) from error


@app.post("/ledger/gateway/deposits")
async def deposit_agent_wallet_to_gateway(request: GatewayDepositRequest) -> dict[str, Any]:
    try:
        parse_positive_atomic(request.amountAtomic)
        return await get_ledger_wallet_client().gateway_deposit(request)
    except (ValueError, RuntimeError) as error:
        raise http_error(error) from error


@app.post("/ledger/gateway/withdrawals")
async def withdraw_agent_wallet_from_gateway(request: GatewayWithdrawalRequest) -> dict[str, Any]:
    try:
        parse_positive_atomic(request.amountAtomic)
        return await get_ledger_wallet_client().gateway_withdraw(request)
    except (ValueError, RuntimeError) as error:
        raise http_error(error) from error


@app.post("/ledger/withdrawals")
async def withdraw_agent_wallet(request: WithdrawalRequest) -> dict[str, Any]:
    withdrawal_id = f"withdrawal_{uuid.uuid4().hex}"
    try:
        route = route_payment_intent(
            PaymentIntent(
                purpose="withdraw Agent Wallet USDC to an external Base address",
                deliveryMode="withdrawal",
                externalService=True,
            )
        )
        if "agent_wallet_settle_ledger_transfer" not in route.get("allowedTools", []):
            raise ValueError("withdrawal route did not allow Circle settlement")

        synced_state = await ledger_state_with_circle_balances()
        synced_account = next(
            (
                account
                for account in synced_state.get("accounts", [])
                if isinstance(account, dict)
                and str(account.get("agentId") or "").strip() == request.agentId
            ),
            None,
        )
        available_atomic = (
            str(synced_account.get("availableAtomic"))
            if isinstance(synced_account, dict)
            and synced_account.get("availableAtomic") is not None
            else None
        )
        get_store().validate_withdrawal(
            agent_id=request.agentId,
            amount_atomic=request.amountAtomic,
            owner_email=request.ownerEmail,
            available_atomic=available_atomic,
        )
        settlement_record = await settle_withdrawal(
            from_agent_id=request.agentId,
            to_address=request.destinationAddress,
            amount_atomic=request.amountAtomic,
            ref_id=withdrawal_id,
        )
        account, entry = get_store().withdraw(
            agent_id=request.agentId,
            destination_address=request.destinationAddress,
            amount_atomic=request.amountAtomic,
            reason=request.reason,
            metadata=request.metadata,
            withdrawal_id=withdrawal_id,
            settlement_record_id=settlement_record.recordId,
            available_atomic=available_atomic,
        )
        chain_record = await record_ledger_chain_event(
            event_type="withdrawal",
            escrow=None,
            entries=[entry],
            extra={
                "withdrawalId": withdrawal_id,
                "agentId": request.agentId,
                "destinationAddress": request.destinationAddress,
                "amountAtomic": request.amountAtomic,
                "settlementRecordId": settlement_record.recordId,
            },
        )
    except (LookupError, ValueError, LedgerChainRecordError, LedgerSettlementError) as error:
        raise http_error(error) from error
    return {
        "withdrawalId": withdrawal_id,
        "account": account.model_dump(),
        "entry": entry.model_dump(),
        "settlementRecord": settlement_record.model_dump(),
        "chainRecord": chain_record.model_dump() if chain_record is not None else None,
        "route": route,
    }


@app.post("/ledger/escrows")
async def create_escrow(request: CreateEscrowRequest) -> dict[str, Any]:
    try:
        escrow, entry = get_store().create_escrow(
            buyer_agent_id=request.buyerAgentId,
            seller_agent_id=request.sellerAgentId,
            amount_atomic=request.amountAtomic,
            task_id=request.taskId,
            description=request.description,
            metadata=request.metadata,
        )
        chain_record = await record_ledger_chain_event(
            event_type="escrow_lock",
            escrow=escrow,
            entries=[entry],
        )
    except (LookupError, ValueError, LedgerChainRecordError) as error:
        raise http_error(error) from error
    return {
        "escrow": escrow.model_dump(),
        "entry": entry.model_dump(),
        "chainRecord": chain_record.model_dump() if chain_record is not None else None,
    }


@app.post("/ledger/transfers")
async def transfer_between_agents(request: AgentTransferRequest) -> dict[str, Any]:
    transfer_id = f"transfer_{uuid.uuid4().hex}"
    try:
        sender_account = get_store().account_by_email(request.fromEmail)
        receiver_account = get_store().account_by_email(request.toEmail)
        get_store().validate_agent_transfer(
            from_agent_id=sender_account.agentId,
            to_agent_id=receiver_account.agentId,
            amount_atomic=request.amountAtomic,
        )
        settlement_record = await settle_agent_transfer(
            from_agent_id=sender_account.agentId,
            to_agent_id=receiver_account.agentId,
            amount_atomic=request.amountAtomic,
            ref_id=transfer_id,
        )
        transfer_metadata = {
            **request.metadata,
            "fromEmail": normalize_email(request.fromEmail),
            "toEmail": normalize_email(request.toEmail),
        }
        sender, receiver, entries = get_store().transfer_between_agents(
            from_agent_id=sender_account.agentId,
            to_agent_id=receiver_account.agentId,
            amount_atomic=request.amountAtomic,
            reason=request.reason,
            metadata=transfer_metadata,
            transfer_id=transfer_id,
            settlement_record_id=settlement_record.recordId,
        )
        chain_record = await record_ledger_chain_event(
            event_type="agent_transfer",
            escrow=None,
            entries=entries,
            extra={
                "transferId": transfer_id,
                "fromAgentId": sender_account.agentId,
                "toAgentId": receiver_account.agentId,
                "fromEmail": normalize_email(request.fromEmail),
                "toEmail": normalize_email(request.toEmail),
                "amountAtomic": request.amountAtomic,
                "settlementRecordId": settlement_record.recordId,
            },
        )
    except (LookupError, ValueError, LedgerChainRecordError, LedgerSettlementError) as error:
        raise http_error(error) from error
    return {
        "transferId": transfer_id,
        "fromAccount": sender.model_dump(),
        "toAccount": receiver.model_dump(),
        "entries": [entry.model_dump() for entry in entries],
        "settlementRecord": settlement_record.model_dump(),
        "chainRecord": chain_record.model_dump() if chain_record is not None else None,
    }


@app.post("/ledger/escrows/{escrow_id}/release")
async def release_escrow(escrow_id: str) -> dict[str, Any]:
    try:
        locked_escrow = get_store().get_escrow(escrow_id)
        if locked_escrow.status != "locked":
            raise ValueError("escrow is not locked")
        settlement_record = await settle_escrow_release(locked_escrow)
        escrow = get_store().release_escrow(escrow_id)
        entries = get_store().entries_for_escrow_event(
            escrow_id=escrow.escrowId,
            entry_type="escrow_release",
        )
        chain_record = await record_ledger_chain_event(
            event_type="escrow_release",
            escrow=escrow,
            entries=entries,
        )
    except (LookupError, ValueError, LedgerChainRecordError, LedgerSettlementError) as error:
        raise http_error(error) from error
    return {
        "escrow": escrow.model_dump(),
        "settlementRecord": settlement_record.model_dump()
        if settlement_record is not None
        else None,
        "chainRecord": chain_record.model_dump() if chain_record is not None else None,
    }


@app.post("/ledger/escrows/{escrow_id}/refund")
async def refund_escrow(escrow_id: str) -> dict[str, Any]:
    try:
        escrow = get_store().refund_escrow(escrow_id)
        entries = get_store().entries_for_escrow_event(
            escrow_id=escrow.escrowId,
            entry_type="escrow_refund",
        )
        chain_record = await record_ledger_chain_event(
            event_type="escrow_refund",
            escrow=escrow,
            entries=entries,
        )
    except (LookupError, ValueError, LedgerChainRecordError) as error:
        raise http_error(error) from error
    return {
        "escrow": escrow.model_dump(),
        "chainRecord": chain_record.model_dump() if chain_record is not None else None,
    }


_ledger_mcp_app = build_mcp_app(get_store)


@asynccontextmanager
async def ledger_app_lifespan(_app: FastAPI):
    async with _ledger_mcp_app.router.lifespan_context(_ledger_mcp_app):
        yield


app.router.lifespan_context = ledger_app_lifespan
app.mount("/", _ledger_mcp_app)
