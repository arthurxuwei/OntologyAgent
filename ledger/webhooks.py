from __future__ import annotations

import base64
import os
from typing import Any, Literal, Optional

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException, Request

from config import (
    DEFAULT_ASSET,
    DEFAULT_BASE_MAINNET_USDC_ASSET_ADDRESS,
    DEFAULT_BASE_SEPOLIA_USDC_ASSET_ADDRESS,
    DEFAULT_CIRCLE_PUBLIC_KEY_BASE_URL,
    GATEWAY_SWEEP_MIN_WALLET_BALANCE_ATOMIC,
)
from models import CircleWebhookEventRecord, GatewayDepositRequest, LedgerEntry
import services
from utils import decimal_usdc_to_atomic_string, now_iso, parse_nonnegative_atomic


GATEWAY_DEPOSIT_LEDGER_METADATA_FIELDS = (
    "depositTransactionId",
    "depositState",
    "approvalTransactionId",
    "approvalState",
    "gatewayBalance",
    "mode",
    "blockchain",
)


def circle_webhook_signature_required() -> bool:
    return os.getenv("CIRCLE_WEBHOOK_VERIFY_SIGNATURE", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


async def verify_circle_webhook_signature(request: Request, body: bytes) -> None:
    if not circle_webhook_signature_required():
        return

    signature = request.headers.get("x-circle-signature")
    key_id = request.headers.get("x-circle-key-id")
    if not signature or not key_id:
        raise HTTPException(status_code=401, detail="Circle webhook signature headers are required")

    api_key = os.getenv("CIRCLE_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="CIRCLE_API_KEY is required for Circle webhook verification")

    base_url = os.getenv(
        "CIRCLE_PUBLIC_KEY_BASE_URL",
        DEFAULT_CIRCLE_PUBLIC_KEY_BASE_URL,
    ).rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{base_url}/{key_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=401, detail="Circle webhook public key lookup failed")

    payload = response.json()
    payload_data = payload.get("data") if isinstance(payload, dict) else None
    public_key_text = payload.get("publicKey") if isinstance(payload, dict) else None
    if not isinstance(public_key_text, str) and isinstance(payload_data, dict):
        public_key_text = payload_data.get("publicKey")
    if not isinstance(public_key_text, str) or not public_key_text.strip():
        raise HTTPException(status_code=401, detail="Circle webhook public key response is invalid")

    try:
        signature_bytes = base64.b64decode(signature, validate=True)
    except Exception:
        try:
            signature_bytes = bytes.fromhex(signature.removeprefix("0x"))
        except ValueError as error:
            raise HTTPException(status_code=401, detail="Circle webhook signature is invalid") from error

    try:
        public_key_material = public_key_text.strip().encode("utf-8")
        if public_key_text.strip().startswith("-----BEGIN"):
            public_key = serialization.load_pem_public_key(public_key_material)
        else:
            public_key = serialization.load_der_public_key(
                base64.b64decode(public_key_material, validate=True)
            )
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            raise ValueError("Circle webhook public key must be an EC public key")
        public_key.verify(signature_bytes, body, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError) as error:
        raise HTTPException(status_code=401, detail="Circle webhook signature verification failed") from error


def circle_webhook_text(value: Any) -> Optional[str]:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def circle_webhook_nested_text(data: dict[str, Any], *keys: str) -> Optional[str]:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return circle_webhook_text(current)


def circle_webhook_notification_id(payload: dict[str, Any]) -> str:
    notification_id = circle_webhook_text(payload.get("notificationId"))
    if notification_id is None:
        raise ValueError("notificationId is required")
    return notification_id


def circle_webhook_notification(payload: dict[str, Any]) -> dict[str, Any]:
    notification = payload.get("notification")
    if isinstance(notification, dict):
        return notification
    return {}


def circle_webhook_completed(notification: dict[str, Any]) -> bool:
    state = (
        circle_webhook_text(notification.get("state"))
        or circle_webhook_text(notification.get("status"))
        or ""
    ).upper()
    return state in {"CONFIRMED", "COMPLETE", "COMPLETED"}


def circle_webhook_inbound(payload: dict[str, Any], notification: dict[str, Any]) -> bool:
    notification_type = (circle_webhook_text(payload.get("notificationType")) or "").lower()
    transaction_type = (
        circle_webhook_text(notification.get("transactionType"))
        or circle_webhook_text(notification.get("type"))
        or ""
    ).upper()
    return notification_type == "transactions.inbound" or transaction_type == "INBOUND"


def circle_webhook_transaction_id(notification: dict[str, Any]) -> Optional[str]:
    return (
        circle_webhook_text(notification.get("id"))
        or circle_webhook_text(notification.get("transactionId"))
    )


def circle_webhook_wallet_id(notification: dict[str, Any]) -> Optional[str]:
    return (
        circle_webhook_text(notification.get("walletId"))
        or circle_webhook_text(notification.get("destinationWalletId"))
        or circle_webhook_nested_text(notification, "destination", "walletId")
    )


def circle_webhook_wallet_address(notification: dict[str, Any]) -> Optional[str]:
    return (
        circle_webhook_text(notification.get("destinationAddress"))
        or circle_webhook_text(notification.get("toAddress"))
        or circle_webhook_text(notification.get("walletAddress"))
        or circle_webhook_nested_text(notification, "destination", "address")
    )


def circle_webhook_usdc_amount_atomic(notification: dict[str, Any]) -> Optional[str]:
    symbol = (
        circle_webhook_text(notification.get("tokenSymbol"))
        or circle_webhook_text(notification.get("symbol"))
        or circle_webhook_text(notification.get("currency"))
        or circle_webhook_nested_text(notification, "token", "symbol")
        or circle_webhook_nested_text(notification, "asset", "symbol")
    )
    if symbol is not None and symbol.upper() != DEFAULT_ASSET:
        return None

    token_address = (
        circle_webhook_text(notification.get("contractAddress"))
        or circle_webhook_text(notification.get("tokenAddress"))
        or circle_webhook_text(notification.get("assetAddress"))
        or circle_webhook_nested_text(notification, "token", "contractAddress")
        or circle_webhook_nested_text(notification, "asset", "address")
    )
    expected_token_address = configured_usdc_asset_address().lower()
    if token_address is not None and token_address.lower() != expected_token_address:
        return None

    amounts = notification.get("amounts")
    amount_value: Any = None
    if isinstance(amounts, list) and amounts:
        first_amount = amounts[0]
        if isinstance(first_amount, dict):
            amount_value = first_amount.get("amount") or first_amount.get("value")
        else:
            amount_value = first_amount
    if amount_value is None:
        amount_value = notification.get("amount")
    if isinstance(amount_value, dict):
        amount_value = amount_value.get("amount") or amount_value.get("value")

    amount_atomic = decimal_usdc_to_atomic_string(amount_value)
    if amount_atomic is None or parse_nonnegative_atomic(amount_atomic) <= 0:
        return None
    return amount_atomic


def configured_usdc_asset_address() -> str:
    configured = os.getenv("X402_USDC_ASSET_ADDRESS")
    if configured and configured.strip():
        return configured.strip()
    chain_profile = os.getenv("CHAIN_PROFILE", "base-sepolia").strip().lower()
    if chain_profile in {"base-mainnet", "base", "mainnet"}:
        return DEFAULT_BASE_MAINNET_USDC_ASSET_ADDRESS
    return DEFAULT_BASE_SEPOLIA_USDC_ASSET_ADDRESS


def circle_wallet_status_usdc_amount_atomic(status: dict[str, Any]) -> Optional[str]:
    balances = status.get("balances")
    if not isinstance(balances, dict):
        return None
    usdc_balance = balances.get(DEFAULT_ASSET)
    if not isinstance(usdc_balance, str):
        return None
    return decimal_usdc_to_atomic_string(usdc_balance)


def circle_webhook_event_record(
    *,
    notification_id: str,
    notification_type: str,
    status: Literal["received", "processed", "skipped", "failed"],
    payload: dict[str, Any],
    transaction_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    wallet_address: Optional[str] = None,
    circle_wallet_id: Optional[str] = None,
    amount_atomic: Optional[str] = None,
    reason: Optional[str] = None,
    gateway_deposit_result: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> CircleWebhookEventRecord:
    existing = services.get_store().get_circle_webhook_event(notification_id)
    current = now_iso()
    return CircleWebhookEventRecord(
        notificationId=notification_id,
        notificationType=notification_type,
        status=status,
        transactionId=transaction_id,
        agentId=agent_id,
        walletAddress=wallet_address,
        circleWalletId=circle_wallet_id,
        amountAtomic=amount_atomic,
        reason=reason,
        gatewayDepositResult=gateway_deposit_result or {},
        rawPayload=payload,
        error=error,
        createdAt=existing.createdAt if existing is not None else current,
        updatedAt=current,
    )


def gateway_deposit_ledger_metadata(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    return {
        key: result[key]
        for key in GATEWAY_DEPOSIT_LEDGER_METADATA_FIELDS
        if key in result and result[key] is not None
    }


async def process_circle_wallet_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    notification_id = circle_webhook_notification_id(payload)
    notification_type = circle_webhook_text(payload.get("notificationType")) or "unknown"
    existing = services.get_store().get_circle_webhook_event(notification_id)
    if existing is not None and existing.status == "skipped":
        return {
            "status": "duplicate",
            "notificationId": notification_id,
            "event": existing.model_dump(),
        }

    notification = circle_webhook_notification(payload)
    transaction_id = circle_webhook_transaction_id(notification)
    wallet_address = circle_webhook_wallet_address(notification)
    circle_wallet_id = circle_webhook_wallet_id(notification)

    def save_skipped(reason: str) -> dict[str, Any]:
        matched_account = services.get_store().find_account_by_wallet(
            wallet_address=wallet_address,
            circle_wallet_id=circle_wallet_id,
        )
        event = services.get_store().save_circle_webhook_event(
            circle_webhook_event_record(
                notification_id=notification_id,
                notification_type=notification_type,
                status="skipped",
                payload=payload,
                transaction_id=transaction_id,
                agent_id=matched_account.agentId if matched_account is not None else None,
                wallet_address=wallet_address,
                circle_wallet_id=circle_wallet_id,
                reason=reason,
            )
        )
        return {"status": "skipped", "notificationId": notification_id, "reason": reason, "event": event.model_dump()}

    if not circle_webhook_inbound(payload, notification):
        return save_skipped("not_inbound_transaction")
    if not circle_webhook_completed(notification):
        return save_skipped("transaction_not_complete")

    amount_atomic = circle_webhook_usdc_amount_atomic(notification)
    if amount_atomic is None:
        return save_skipped("not_positive_usdc")

    account = services.get_store().find_account_by_wallet(
        wallet_address=wallet_address,
        circle_wallet_id=circle_wallet_id,
    )
    if account is None:
        return save_skipped("wallet_not_bound")

    gateway_ref_id = f"circle-webhook:{notification_id}"

    def matching_entry(entry_type: str, dashboard_status: str) -> Optional[LedgerEntry]:
        for entry in services.get_store().load().entries:
            if entry.entryType != entry_type or entry.agentId != account.agentId:
                continue
            metadata = entry.metadata
            same_notification = metadata.get("notificationId") == notification_id
            same_transaction = metadata.get("circleTransactionId") == transaction_id
            if (
                metadata.get("dashboardStatus") == dashboard_status
                and (same_notification or same_transaction)
            ):
                return entry
        return None

    def pending_metadata() -> dict[str, Any]:
        return {
            "dashboardStatus": "pending_inbound_chain",
            "amountAtomic": amount_atomic,
            "counterparty": "External wallet",
            "gatewayStage": "gateway_crediting",
            "txHash": transaction_id,
            "network": "Base",
            "circleTransactionId": transaction_id,
            "notificationId": notification_id,
            "gatewayRefId": gateway_ref_id,
        }

    def credited_metadata(
        pending_entry: LedgerEntry,
        credited_amount_atomic: str,
        result: Any,
    ) -> dict[str, Any]:
        pending = pending_entry.metadata
        pending_transaction_id = pending.get("circleTransactionId") or transaction_id
        pending_notification_id = pending.get("notificationId") or notification_id
        pending_gateway_ref_id = pending.get("gatewayRefId") or gateway_ref_id
        return {
            "dashboardStatus": "credited",
            "amountAtomic": credited_amount_atomic,
            "counterparty": pending.get("counterparty") or "External wallet",
            "linkedEntryId": pending_entry.entryId,
            "txHash": pending.get("txHash") or pending_transaction_id,
            "network": pending.get("network") or "Base",
            "circleTransactionId": pending_transaction_id,
            "notificationId": pending_notification_id,
            "gatewayRefId": pending_gateway_ref_id,
            **gateway_deposit_ledger_metadata(result),
        }

    def credit_linked_to_pending(pending: LedgerEntry) -> Optional[LedgerEntry]:
        state = services.get_store().load()
        pending_transaction_id = pending.metadata.get("circleTransactionId")
        for entry in state.entries:
            if entry.entryType != "credit" or entry.agentId != account.agentId:
                continue
            metadata = entry.metadata
            if metadata.get("linkedEntryId") == pending.entryId:
                return entry
            if (
                pending_transaction_id
                and metadata.get("dashboardStatus") == "credited"
                and metadata.get("circleTransactionId") == pending_transaction_id
            ):
                return entry
        return None

    def pending_inbounds_covered_by_sweep(swept_amount_atomic: str) -> list[LedgerEntry]:
        remaining = parse_nonnegative_atomic(swept_amount_atomic)
        covered: list[LedgerEntry] = []
        state = services.get_store().load()
        for entry in state.entries:
            if entry.entryType != "pending_inbound" or entry.agentId != account.agentId:
                continue
            if entry.metadata.get("dashboardStatus") != "pending_inbound_chain":
                continue
            if credit_linked_to_pending(entry) is not None:
                continue
            amount_text = entry.metadata.get("amountAtomic")
            if not isinstance(amount_text, str):
                continue
            amount = parse_nonnegative_atomic(amount_text)
            if amount <= 0 or amount > remaining:
                continue
            covered.append(entry)
            remaining -= amount
        return covered

    def mark_pending_event_processed_by_batch(
        pending: LedgerEntry,
        result: dict[str, Any],
    ) -> None:
        pending_notification_id = pending.metadata.get("notificationId")
        if not isinstance(pending_notification_id, str):
            return
        event = services.get_store().get_circle_webhook_event(pending_notification_id)
        if event is None or event.status == "processed":
            return
        services.get_store().save_circle_webhook_event(
            event.model_copy(
                update={
                    "status": "processed",
                    "reason": "gateway_deposit_completed_by_batch_sweep",
                    "gatewayDepositResult": result,
                    "error": None,
                    "updatedAt": now_iso(),
                }
            )
        )

    pending_entry = matching_entry("pending_inbound", "pending_inbound_chain")
    credited_entry = matching_entry("credit", "credited")
    if existing is not None and existing.status == "processed" and pending_entry and credited_entry:
        return {
            "status": "duplicate",
            "notificationId": notification_id,
            "event": existing.model_dump(),
        }
    if existing is None and credited_entry is not None:
        duplicate_event = services.get_store().save_circle_webhook_event(
            circle_webhook_event_record(
                notification_id=notification_id,
                notification_type=notification_type,
                status="processed",
                payload=payload,
                transaction_id=transaction_id,
                agent_id=account.agentId,
                wallet_address=wallet_address,
                circle_wallet_id=circle_wallet_id,
                amount_atomic=amount_atomic,
                reason="duplicate_gateway_deposit_completed",
            )
        )
        return {
            "status": "duplicate",
            "notificationId": notification_id,
            "event": duplicate_event.model_dump(),
        }
    if existing is not None and existing.status != "processed" and credited_entry is not None:
        duplicate_event = services.get_store().save_circle_webhook_event(
            existing.model_copy(
                update={
                    "status": "processed",
                    "reason": "duplicate_gateway_deposit_completed",
                    "updatedAt": now_iso(),
                }
            )
        )
        return {
            "status": "duplicate",
            "notificationId": notification_id,
            "event": duplicate_event.model_dump(),
        }

    if existing is not None and existing.status in {"received", "processed"}:
        received = existing
    else:
        received = services.get_store().save_circle_webhook_event(
            circle_webhook_event_record(
                notification_id=notification_id,
                notification_type=notification_type,
                status="received",
                payload=payload,
                transaction_id=transaction_id,
                agent_id=account.agentId,
                wallet_address=wallet_address,
                circle_wallet_id=circle_wallet_id,
                amount_atomic=amount_atomic,
                reason="gateway_deposit_started",
            )
        )

    gateway_deposit_result: dict[str, Any] = {}
    swept_amount_atomic: Optional[str] = None
    credited_amount_atomic = (
        existing.amountAtomic
        if existing is not None and existing.amountAtomic is not None
        else amount_atomic
    )
    if credited_entry is None and existing is not None and existing.status == "processed":
        if pending_entry is None:
            _pending_account, pending_entry = services.get_store().record_dashboard_event(
                entry_type="pending_inbound",
                agent_id=account.agentId,
                reason="external top-up detected",
                metadata=pending_metadata(),
            )
        gateway_deposit_result = existing.gatewayDepositResult
    elif credited_entry is None:
        wallet_client = services.get_ledger_wallet_client()
        try:
            wallet_status = await wallet_client.status(
                wallet_address=wallet_address,
                circle_wallet_id=circle_wallet_id,
            )
            wallet_balance_atomic = circle_wallet_status_usdc_amount_atomic(wallet_status)
            if wallet_balance_atomic is None:
                raise RuntimeError("wallet status did not include a USDC balance")
            if int(wallet_balance_atomic) < GATEWAY_SWEEP_MIN_WALLET_BALANCE_ATOMIC:
                skipped = services.get_store().save_circle_webhook_event(
                    received.model_copy(
                        update={
                            "status": "skipped",
                            "reason": "wallet_balance_not_above_gateway_threshold",
                            "gatewayDepositResult": {
                                "walletBalanceAtomic": wallet_balance_atomic,
                                "thresholdAtomic": str(GATEWAY_SWEEP_MIN_WALLET_BALANCE_ATOMIC),
                            },
                            "updatedAt": now_iso(),
                        }
                    )
                )
                return {
                    "status": "skipped",
                    "notificationId": notification_id,
                    "reason": "wallet_balance_not_above_gateway_threshold",
                    "event": skipped.model_dump(),
                }

            if pending_entry is None:
                _pending_account, pending_entry = services.get_store().record_dashboard_event(
                    entry_type="pending_inbound",
                    agent_id=account.agentId,
                    reason="external top-up detected",
                    metadata=pending_metadata(),
                )

            result = await wallet_client.gateway_deposit(
                GatewayDepositRequest(
                    agentId=account.agentId,
                    amountAtomic=wallet_balance_atomic,
                    refId=gateway_ref_id,
                )
            )
            swept_amount_atomic = wallet_balance_atomic
        except Exception as error:
            services.get_store().save_circle_webhook_event(
                received.model_copy(
                    update={
                        "status": "failed",
                        "reason": "gateway_deposit_failed",
                        "error": str(error),
                        "updatedAt": now_iso(),
                    }
                )
            )
            raise RuntimeError(f"Gateway deposit failed: {error}") from error
        gateway_deposit_result = result if isinstance(result, dict) else {}

    if pending_entry is None:
        _pending_account, pending_entry = services.get_store().record_dashboard_event(
            entry_type="pending_inbound",
            agent_id=account.agentId,
            reason="external top-up detected",
            metadata=pending_metadata(),
        )

    credited_account = services.get_store().ensure_account(account.agentId)
    if credited_entry is None:
        pending_entries = [pending_entry]
        if swept_amount_atomic is not None:
            pending_entries = pending_inbounds_covered_by_sweep(swept_amount_atomic)
            if pending_entry not in pending_entries:
                pending_entries.append(pending_entry)

        for candidate in pending_entries:
            candidate_amount_atomic = candidate.metadata.get("amountAtomic")
            if not isinstance(candidate_amount_atomic, str):
                candidate_amount_atomic = credited_amount_atomic
            existing_credit = credit_linked_to_pending(candidate)
            if existing_credit is not None:
                if candidate.entryId == pending_entry.entryId:
                    credited_entry = existing_credit
                continue
            credited_account, created_credit = services.get_store().credit(
                agent_id=account.agentId,
                amount_atomic=candidate_amount_atomic,
                reason="Gateway Wallet credited",
                metadata=credited_metadata(
                    candidate,
                    candidate_amount_atomic,
                    gateway_deposit_result,
                ),
            )
            mark_pending_event_processed_by_batch(candidate, gateway_deposit_result)
            if candidate.entryId == pending_entry.entryId:
                credited_entry = created_credit

    if credited_entry is None:
        credited_entry = credit_linked_to_pending(pending_entry)

    processed = services.get_store().save_circle_webhook_event(
        received.model_copy(
            update={
                "status": "processed",
                "reason": "gateway_deposit_completed",
                "gatewayDepositResult": gateway_deposit_result,
                "updatedAt": now_iso(),
            }
        )
    )
    return {
        "status": "processed",
        "notificationId": notification_id,
        "event": processed.model_dump(),
        "pendingEntry": pending_entry.model_dump(),
        "creditedEntry": credited_entry.model_dump(),
        "account": credited_account.model_dump(),
    }
