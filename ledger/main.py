from __future__ import annotations

import json
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
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from mcp_tools import (
    agent_wallet_create_escrow_tool,
    agent_wallet_create_onramp_session_tool,
    agent_wallet_get_or_create_tool,
    agent_wallet_get_ledger_state_tool,
    agent_wallet_refund_escrow_tool,
    agent_wallet_release_escrow_tool,
    build_mcp_app,
    route_payment_intent_tool,
)
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


DEFAULT_ASSET = "USDC"
DEFAULT_LEDGER_STATE_PATH = "ledger/data/offchain_ledger.json"
DEFAULT_COINBASE_API_BASE_URL = "https://api.developer.coinbase.com"
DEFAULT_COINBASE_TOKEN_PATH = "/onramp/v1/token"
DEFAULT_COINBASE_HOSTED_URL = "https://pay.coinbase.com/buy/select-asset"
DEFAULT_CHAIN_MCP_URL = "http://chain-mcp:8091/mcp/"
DEFAULT_SETTLEMENT_MCP_URL = "http://circle-mcp:8093/mcp/"
DEFAULT_WALLET_MCP_URL = "http://circle-mcp:8093/mcp/"
DEFAULT_CHAIN_RECORDER_ADDRESS = "0x000000000000000000000000000000000000dEaD"
LEDGER_CONSOLE_PATH = Path(__file__).resolve().parent / "web" / "index.html"

app = FastAPI(title="Chief offchain ledger")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LedgerAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agentId: str
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
    entryType: Literal["credit", "escrow_lock", "escrow_release", "escrow_refund"]
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
    eventType: Literal["credit", "escrow_lock", "escrow_release", "escrow_refund"]
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
    eventType: Literal["escrow_release"]
    status: Literal["submitted", "failed"]
    settlementTool: str = "agent_wallet_settle_ledger_transfer"
    chainMcpUrl: str
    escrowId: str
    fromAgentId: str
    toAgentId: str
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


class CreateEscrowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    buyerAgentId: str
    sellerAgentId: str
    amountAtomic: str
    taskId: Optional[str] = None
    description: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


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


def add_atomic(left: str, delta: int) -> str:
    result = int(left) + delta
    if result < 0:
        raise ValueError("ledger balance cannot become negative")
    return str(result)


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
        chain_mcp_url: str,
        recorder_address: str,
        timeout_seconds: float,
        max_payload_bytes: int,
        require_success: bool,
    ) -> None:
        self.enabled = enabled
        self.chain_mcp_url = chain_mcp_url
        self.recorder_address = recorder_address
        self.timeout_seconds = timeout_seconds
        self.max_payload_bytes = max_payload_bytes
        self.require_success = require_success

    async def submit(
        self,
        *,
        event_type: Literal["credit", "escrow_lock", "escrow_release", "escrow_refund"],
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
            "chainMcpUrl": self.chain_mcp_url,
            "recorderAddress": self.recorder_address,
            "escrowId": escrow.escrowId if escrow is not None else None,
            "entryIds": [entry.entryId for entry in entries],
            "payload": payload,
            "createdAt": current,
            "updatedAt": current,
        }

        try:
            data = encode_ledger_record_payload(payload, self.max_payload_bytes)
            result = await self._call_chain_submit_execution(data)
            structured = result.get("structuredContent")
            if not isinstance(structured, dict):
                raise RuntimeError("chain MCP response did not include structuredContent")
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
                raise RuntimeError("chain MCP response did not include a transaction hash")
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
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "chain_submit_execution",
                "arguments": {
                    "to": self.recorder_address,
                    "valueEth": "0",
                    "data": data,
                },
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.chain_mcp_url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                json=request,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"chain MCP request failed: HTTP {response.status_code} {response.text}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("chain MCP response was not a JSON object")
        if "error" in payload:
            raise RuntimeError(f"chain MCP returned error: {payload['error']}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("chain MCP response did not include result")
        return result


class LedgerChainRecordError(RuntimeError):
    def __init__(self, record: LedgerChainRecord) -> None:
        super().__init__(record.error or "ledger chain record failed")
        self.record = record


class LedgerSettlementClient:
    def __init__(
        self,
        *,
        enabled: bool,
        settlement_mcp_url: str,
        timeout_seconds: float,
        require_success: bool,
    ) -> None:
        self.enabled = enabled
        self.settlement_mcp_url = settlement_mcp_url
        self.timeout_seconds = timeout_seconds
        self.require_success = require_success

    async def submit_release(self, escrow: EscrowRecord) -> Optional[LedgerSettlementRecord]:
        if not self.enabled:
            return None

        current = now_iso()
        base_record = {
            "recordId": f"settle_{uuid.uuid4().hex}",
            "eventType": "escrow_release",
            "chainMcpUrl": self.settlement_mcp_url,
            "escrowId": escrow.escrowId,
            "fromAgentId": escrow.buyerAgentId,
            "toAgentId": escrow.sellerAgentId,
            "asset": escrow.asset,
            "amountAtomic": escrow.amountAtomic,
            "createdAt": current,
            "updatedAt": current,
        }
        try:
            result = await self._call_settlement_tool(escrow)
            transfer = self._extract_tool_content(result)
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

    async def _call_settlement_tool(self, escrow: EscrowRecord) -> dict[str, Any]:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "agent_wallet_settle_ledger_transfer",
                "arguments": {
                    "fromAgentId": escrow.buyerAgentId,
                    "toAgentId": escrow.sellerAgentId,
                    "amountAtomic": escrow.amountAtomic,
                    "refId": f"{escrow.escrowId}:release",
                },
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.settlement_mcp_url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                json=request,
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"settlement MCP request failed: HTTP {response.status_code} {response.text}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("settlement MCP response was not a JSON object")
        if "error" in payload:
            raise RuntimeError(f"settlement MCP returned error: {payload['error']}")
        result = payload.get("result")
        if isinstance(result, dict) and result.get("isError") is True:
            content = self._extract_tool_content(payload)
            if content.get("error") is not None:
                raise RuntimeError(json.dumps(content["error"], sort_keys=True))
            raise RuntimeError("settlement MCP tool returned isError=true")
        return payload

    @staticmethod
    def _extract_tool_content(result: dict[str, Any]) -> dict[str, Any]:
        content = result.get("result", {}).get("content", [])
        if not isinstance(content, list) or not content:
            return {}
        first = content[0]
        if not isinstance(first, dict):
            return {}
        text = first.get("text")
        if not isinstance(text, str):
            return {}
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return {}
        return parsed


class LedgerSettlementError(RuntimeError):
    def __init__(self, record: LedgerSettlementRecord) -> None:
        super().__init__(record.error or "ledger settlement failed")
        self.record = record


class LedgerWalletClient:
    def __init__(self, *, wallet_mcp_url: str, timeout_seconds: float) -> None:
        self.wallet_mcp_url = wallet_mcp_url
        self.timeout_seconds = timeout_seconds

    async def get_or_create(self, request: AgentWalletRequest) -> dict[str, Any]:
        payload = await self._call_wallet_tool(
            "agent_wallet_get_or_create",
            {
                "agentName": request.agentName,
                "agentId": request.agentId,
                "email": request.email,
                "walletAddress": request.walletAddress,
                "circleWalletId": request.circleWalletId,
                "agentDescription": request.agentDescription,
            },
        )
        wallet = self._extract_tool_content(payload)
        if not wallet:
            raise RuntimeError("wallet MCP response did not include wallet content")
        return wallet

    async def status(
        self,
        *,
        wallet_address: Optional[str],
        circle_wallet_id: Optional[str],
    ) -> dict[str, Any]:
        payload = await self._call_wallet_tool(
            "agent_wallet_status",
            {
                "walletAddress": wallet_address,
                "circleWalletId": circle_wallet_id,
            },
        )
        wallet = self._extract_tool_content(payload)
        if not wallet:
            raise RuntimeError("wallet MCP response did not include wallet status content")
        return wallet

    async def _call_wallet_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": {
                    key: value
                    for key, value in arguments.items()
                    if value is not None
                },
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.wallet_mcp_url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                json=request,
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"wallet MCP request failed: HTTP {response.status_code} {response.text}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("wallet MCP response was not a JSON object")
        if "error" in payload:
            raise RuntimeError(f"wallet MCP returned error: {payload['error']}")
        result = payload.get("result")
        if isinstance(result, dict) and result.get("isError") is True:
            content = self._extract_tool_content(payload)
            if content.get("error") is not None:
                raise RuntimeError(json.dumps(content["error"], sort_keys=True))
            raise RuntimeError("wallet MCP tool returned isError=true")
        return payload

    @staticmethod
    def _extract_tool_content(result: dict[str, Any]) -> dict[str, Any]:
        content = result.get("result", {}).get("content", [])
        if not isinstance(content, list) or not content:
            return {}
        first = content[0]
        if not isinstance(first, dict):
            return {}
        text = first.get("text")
        if not isinstance(text, str):
            return {}
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return {}
        return parsed


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
        wallet_address: Optional[str],
        circle_wallet_id: Optional[str],
    ) -> LedgerAccount:
        def mutate(state: LedgerState) -> LedgerAccount:
            account, account_index = self._account_for_update(
                state, agent_id, create=True
            )
            updates: dict[str, Any] = {"updatedAt": now_iso()}
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
        entry_type: Literal["credit", "escrow_lock", "escrow_release", "escrow_refund"],
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
        chain_mcp_url=os.getenv("LEDGER_CHAIN_MCP_URL", DEFAULT_CHAIN_MCP_URL),
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
        settlement_mcp_url=os.getenv("LEDGER_SETTLEMENT_MCP_URL", DEFAULT_SETTLEMENT_MCP_URL),
        timeout_seconds=float(os.getenv("LEDGER_SETTLEMENT_TIMEOUT_SECONDS", "60")),
        require_success=os.getenv("LEDGER_SETTLEMENT_REQUIRE_SUCCESS", "false").lower()
        in {"1", "true", "yes", "on"},
    )


@lru_cache(maxsize=1)
def get_ledger_wallet_client() -> LedgerWalletClient:
    return LedgerWalletClient(
        wallet_mcp_url=os.getenv("LEDGER_WALLET_MCP_URL", DEFAULT_WALLET_MCP_URL),
        timeout_seconds=float(os.getenv("LEDGER_WALLET_TIMEOUT_SECONDS", "60")),
    )


def ledger_chain_payload(
    *,
    event_type: Literal["credit", "escrow_lock", "escrow_release", "escrow_refund"],
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
    event_type: Literal["credit", "escrow_lock", "escrow_release", "escrow_refund"],
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
            status_code=502,
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
def ledger_console() -> FileResponse:
    return FileResponse(LEDGER_CONSOLE_PATH, media_type="text/html")


@app.get("/ledger/state")
async def get_ledger_state() -> dict[str, Any]:
    return await ledger_state_with_circle_balances()


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
