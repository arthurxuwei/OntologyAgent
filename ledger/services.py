from __future__ import annotations

import asyncio
import json
import logging
import os
from functools import lru_cache
from typing import Any, Literal, Optional

from fastapi import HTTPException

from clients import (
    CoinbaseAuth,
    CoinbaseOnrampClient,
    LedgerChainRecorder,
    LedgerChainRecordError,
    LedgerSettlementClient,
    LedgerSettlementError,
    LedgerWalletClient,
)
from config import (
    DEFAULT_ASSET,
    DEFAULT_CHAIN_HTTP_URL,
    DEFAULT_CHAIN_RECORDER_ADDRESS,
    DEFAULT_COINBASE_API_BASE_URL,
    DEFAULT_COINBASE_HOSTED_URL,
    DEFAULT_COINBASE_TOKEN_PATH,
    DEFAULT_LEDGER_DB_PATH,
    DEFAULT_LEDGER_STATE_PATH,
    DEFAULT_SETTLEMENT_HTTP_URL,
    DEFAULT_WALLET_HTTP_URL,
)
from models import (
    AgentProfile,
    AgentWalletRequest,
    CreateAgentProfileRequest,
    LedgerChainRecord,
    LedgerEntry,
    LedgerSettlementRecord,
)
from store import OffchainLedgerStore
from utils import (
    decimal_usdc_to_atomic_string,
    generate_agent_id,
    normalize_email,
    normalize_wallet_account_type,
    now_iso,
)

logger = logging.getLogger("kovaloop.ledger")


@lru_cache(maxsize=1)
def get_store() -> OffchainLedgerStore:
    return OffchainLedgerStore(
        os.getenv("LEDGER_DB_PATH", DEFAULT_LEDGER_DB_PATH),
        legacy_json_path=os.getenv("LEDGER_STATE_PATH", DEFAULT_LEDGER_STATE_PATH),
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
        "agent_transfer",
        "withdrawal",
    ],
    entries: list[LedgerEntry],
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "kovaloop-ledger-event",
        "eventType": event_type,
        "entryIds": [entry.entryId for entry in entries],
        "entries": [
            {
                "entryId": entry.entryId,
                "entryType": entry.entryType,
                "agentId": entry.agentId,
                "asset": entry.asset,
                "availableDeltaAtomic": entry.availableDeltaAtomic,
                "reason": entry.reason,
                "createdAt": entry.createdAt,
            }
            for entry in entries
        ],
        "createdAt": now_iso(),
    }
    if extra:
        payload["extra"] = extra
    return payload


async def record_ledger_chain_event(
    *,
    event_type: Literal[
        "credit",
        "agent_transfer",
        "withdrawal",
    ],
    entries: list[LedgerEntry],
    extra: Optional[dict[str, Any]] = None,
) -> Optional[LedgerChainRecord]:
    recorder = get_chain_recorder()
    record = await recorder.submit(
        event_type=event_type,
        entries=entries,
        payload=ledger_chain_payload(
            event_type=event_type,
            entries=entries,
            extra=extra,
        ),
    )
    if record is None:
        return None
    return get_store().add_chain_record(record)


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
    owner_email = normalize_email(request.email)
    if owner_email is None:
        raise ValueError("email is required")
    request = request.model_copy(update={"email": owner_email})

    wallet = await get_ledger_wallet_client().get_or_create(request)
    binding = wallet.get("binding")
    binding_agent_id = binding.get("agentId") if isinstance(binding, dict) else None
    if binding_agent_id is not None and binding_agent_id != request.agentId:
        raise ValueError("circle wallet binding agentId mismatch")

    wallet_address = wallet.get("walletAddress")
    circle_wallet_id = wallet.get("circleWalletId")
    account_type = normalize_wallet_account_type(wallet.get("accountType"))
    if isinstance(binding, dict):
        wallet_address = wallet_address or binding.get("walletAddress")
        circle_wallet_id = circle_wallet_id or binding.get("circleWalletId")
        account_type = account_type or normalize_wallet_account_type(
            binding.get("accountType")
        )
    wallet_address = (
        wallet_address if isinstance(wallet_address, str) and wallet_address else None
    )
    circle_wallet_id = (
        circle_wallet_id if isinstance(circle_wallet_id, str) and circle_wallet_id else None
    )
    if (wallet_address is not None or circle_wallet_id is not None) and account_type != "EOA":
        raise ValueError("claim wallet must be an EOA Circle wallet")
    if wallet_address is not None or circle_wallet_id is not None:
        account = get_store().bind_account_wallet(
            agent_id=request.agentId,
            agent_name=request.agentName,
            email=request.email,
            wallet_address=wallet_address,
            circle_wallet_id=circle_wallet_id,
            account_type=account_type,
        )
    else:
        account = get_store().bind_account_wallet(
            agent_id=request.agentId,
            agent_name=request.agentName,
            email=request.email,
            wallet_address=None,
            circle_wallet_id=None,
            account_type=account_type,
        )
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


async def ledger_state_with_circle_balances(agent_id: Optional[str] = None) -> dict[str, Any]:
    if agent_id:
        state = get_store().load_for_agent(agent_id).model_dump()
    else:
        state = get_store().load().model_dump()
    accounts = state.get("accounts")
    if not isinstance(accounts, list):
        return state
    await enrich_accounts_with_circle_balances(accounts)
    return state


def _circle_balance_concurrency() -> int:
    try:
        value = int(os.getenv("LEDGER_CIRCLE_BALANCE_CONCURRENCY", "8"))
    except ValueError:
        return 8
    return max(1, value)


CIRCLE_BALANCE_CONCURRENCY = _circle_balance_concurrency()


async def enrich_accounts_with_circle_balances(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(accounts, list):
        return accounts

    wallet_client = get_ledger_wallet_client()
    semaphore = asyncio.Semaphore(CIRCLE_BALANCE_CONCURRENCY)

    async def enrich_one(account: Any) -> None:
        if not isinstance(account, dict):
            return
        wallet_address = account.get("walletAddress")
        circle_wallet_id = account.get("circleWalletId")
        if not isinstance(wallet_address, str) and not isinstance(circle_wallet_id, str):
            return
        async with semaphore:
            try:
                status = await wallet_client.status(
                    wallet_address=wallet_address if isinstance(wallet_address, str) else None,
                    circle_wallet_id=circle_wallet_id if isinstance(circle_wallet_id, str) else None,
                )
            except Exception as error:
                account["circleBalanceError"] = str(error)
                return
        apply_circle_status_to_account(account, status)

    await asyncio.gather(*(enrich_one(account) for account in accounts))
    return accounts


def apply_circle_status_to_account(account: dict[str, Any], status: dict[str, Any]) -> None:
    account_type = normalize_wallet_account_type(status.get("accountType"))
    if account_type is not None:
        account["accountType"] = account_type
    balances = status.get("balances")
    if isinstance(balances, dict):
        usdc_balance = balances.get(DEFAULT_ASSET)
        if not isinstance(usdc_balance, str):
            usdc_balance = "0"
        account["circleUsdcBalance"] = usdc_balance
        circle_available_atomic = decimal_usdc_to_atomic_string(usdc_balance)
        if circle_available_atomic is not None:
            account["circleAvailableAtomic"] = circle_available_atomic
    gateway_balance = status.get("gatewayBalance")
    if isinstance(gateway_balance, dict):
        formatted_available = gateway_balance.get("formattedAvailable")
        formatted_total = gateway_balance.get("formattedTotal")
        formatted_withdrawing = gateway_balance.get("formattedWithdrawing")
        formatted_withdrawable = gateway_balance.get("formattedWithdrawable")
        formatted_pending_deposits = gateway_balance.get("formattedPendingDeposits")
        formatted_pending_batch = gateway_balance.get("formattedPendingBatch")
        if isinstance(formatted_available, str):
            account["gatewayUsdcAvailable"] = formatted_available
        if isinstance(formatted_total, str):
            account["gatewayUsdcTotal"] = formatted_total
        if isinstance(formatted_withdrawing, str):
            account["gatewayUsdcWithdrawing"] = formatted_withdrawing
        if isinstance(formatted_withdrawable, str):
            account["gatewayUsdcWithdrawable"] = formatted_withdrawable
        if isinstance(formatted_pending_deposits, str):
            account["gatewayUsdcPendingDeposits"] = formatted_pending_deposits
        if isinstance(formatted_pending_batch, str):
            account["gatewayUsdcPendingBatch"] = formatted_pending_batch
        for source_key, target_key in {
            "availableAtomic": "gatewayAvailableAtomic",
            "totalAtomic": "gatewayTotalAtomic",
            "withdrawingAtomic": "gatewayWithdrawingAtomic",
            "withdrawableAtomic": "gatewayWithdrawableAtomic",
            "pendingDepositsAtomic": "gatewayPendingDepositsAtomic",
            "pendingBatchAtomic": "gatewayPendingBatchAtomic",
        }.items():
            value = gateway_balance.get(source_key)
            if isinstance(value, str):
                account[target_key] = value
    gateway_balance_error = status.get("gatewayBalanceError")
    if isinstance(gateway_balance_error, str):
        account["gatewayBalanceError"] = gateway_balance_error


async def enriched_account_payloads(accounts: list[Any]) -> list[dict[str, Any]]:
    payloads = [
        account.model_dump() if hasattr(account, "model_dump") else dict(account)
        for account in accounts
    ]
    return await enrich_accounts_with_circle_balances(payloads)


def assemble_profile_payload(profile: AgentProfile) -> dict[str, Any]:
    data = profile.model_dump()
    data.pop("credentialPublicKey", None)
    data.pop("credentialStatus", None)
    return data


def _generate_unique_agent_id() -> str:
    for _ in range(5):
        candidate = generate_agent_id()
        if get_store().get_agent_profile(candidate) is None:
            return candidate
    raise RuntimeError("failed to generate a unique agentId")


async def create_agent_profile_with_wallet(
    request: CreateAgentProfileRequest,
) -> dict[str, Any]:
    owner_email = normalize_email(request.ownerEmail)
    if owner_email is None:
        raise ValueError("ownerEmail is required")

    agent_id = _generate_unique_agent_id()
    stamp = now_iso()
    profile = AgentProfile(
        agentId=agent_id,
        agentName=request.agentName,
        ownerEmail=owner_email,
        description=request.description,
        eigenflux=request.eigenflux,
        credentialPublicKey=request.credentialPublicKey,
        createdAt=stamp,
        updatedAt=stamp,
    )
    saved = get_store().create_agent_profile(profile=profile)

    # MVP: eagerly provision the agent's Circle wallet at profile creation.
    await get_or_create_agent_wallet(
        AgentWalletRequest(
            agentName=saved.agentName,
            agentId=saved.agentId,
            email=owner_email,
            agentDescription=saved.description,
        )
    )
    return {"profile": assemble_profile_payload(saved)}


def rotate_agent_credential(agent_id: str, public_key: str) -> dict[str, Any]:
    updated = get_store().update_agent_credential(agent_id, public_key)
    payload = assemble_profile_payload(updated)
    payload["credentialPublicKey"] = updated.credentialPublicKey
    payload["credentialStatus"] = updated.credentialStatus
    return payload
