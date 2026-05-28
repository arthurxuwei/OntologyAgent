from __future__ import annotations

import base64
import json
import secrets
import time
import uuid
from typing import Any, Literal, Optional
from urllib.parse import urlencode

import httpx
import jwt
from chain_client import ChainHttpClient
from circle_client import CircleHttpClient
from cryptography.hazmat.primitives.asymmetric import ed25519

from config import DEFAULT_ASSET
from models import (
    AgentWalletRequest,
    EscrowRecord,
    GatewayDepositRequest,
    GatewayWithdrawalRequest,
    LedgerChainRecord,
    LedgerEntry,
    LedgerSettlementRecord,
)
from utils import normalize_evm_address, now_iso


def first_string(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


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
            "chainHttpUrl": self.chain_http_url,
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
                actionResult=structured,
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
            "settlementHttpUrl": self.settlement_http_url,
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
            result = await self._submit_settlement_transfer(
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
                actionResult=result,
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
            "settlementHttpUrl": self.settlement_http_url,
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
            client = CircleHttpClient(
                base_url=self.settlement_http_url,
                timeout_seconds=self.timeout_seconds,
                transport=self.transport,
            )
            result = await client.gateway_withdraw(
                {
                    "agentId": from_agent_id,
                    "amountAtomic": amount_atomic,
                    "recipientAddress": destination,
                    "refId": ref_id,
                }
            )
            transfer = result
            if transfer.get("error") is not None:
                raise RuntimeError(json.dumps(transfer["error"], sort_keys=True))
            return LedgerSettlementRecord(
                **base_record,
                status="submitted",
                transactionId=first_string(
                    transfer.get("transactionId"),
                    transfer.get("mintTransactionId"),
                    transfer.get("gatewayTransferId"),
                ),
                transactionHash=first_string(
                    transfer.get("transactionHash"),
                    transfer.get("mintTransactionHash"),
                ),
                transactionState=first_string(
                    transfer.get("state"),
                    transfer.get("mintState"),
                ),
                mode=transfer.get("mode") if isinstance(transfer.get("mode"), str) else None,
                actionResult=result,
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

    async def _submit_settlement_transfer(
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

    async def gas_topup_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = CircleHttpClient(
            base_url=self.wallet_http_url,
            timeout_seconds=self.timeout_seconds,
            transport=self.transport,
        )
        result = await client.gas_topup_webhook(payload)
        if not result:
            raise RuntimeError("wallet REST response did not include gas top-up webhook content")
        return result

    async def gas_topup_resume(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = CircleHttpClient(
            base_url=self.wallet_http_url,
            timeout_seconds=self.timeout_seconds,
            transport=self.transport,
        )
        result = await client.gas_topup_resume(payload)
        if not result:
            raise RuntimeError("wallet REST response did not include gas top-up resume content")
        return result

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
