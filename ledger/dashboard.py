from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from utils import (
    atomic_decimal,
    atomic_to_usdc,
    claim_code_for_account,
    decimal_usdc_to_atomic_string,
    normalize_email,
    normalize_wallet_account_type,
    parse_dashboard_amount_atomic,
    short_address,
)


FINAL_GATEWAY_TRANSFER_STATES = {"SETTLED", "COMPLETE", "COMPLETED", "CONFIRMED"}


def dashboard_claimed_days_ago(account: dict[str, Any]) -> int:
    claimed_at = account.get("dashboardClaimedAt")
    if not isinstance(claimed_at, str) or not claimed_at.strip():
        return 0
    try:
        claimed_at_dt = datetime.fromisoformat(claimed_at.strip().replace("Z", "+00:00"))
    except ValueError:
        return 0
    if claimed_at_dt.tzinfo is None:
        claimed_at_dt = claimed_at_dt.replace(tzinfo=timezone.utc)
    elapsed = datetime.now(timezone.utc) - claimed_at_dt.astimezone(timezone.utc)
    return max(elapsed.days, 0)


def dashboard_counterparty(entry: dict[str, Any]) -> str:
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

    entry_type = str(entry.get("entryType") or "")
    reason = str(entry.get("reason") or "").strip()
    if "onramp" in entry_type or "onramp" in reason.lower():
        return "Coinbase Onramp"
    return reason or "Ledger"


def dashboard_base_amount_atomic(entry: dict[str, Any]) -> Decimal:
    return abs(atomic_decimal(entry.get("availableDeltaAtomic")))


def dashboard_amount_display(amount_atomic: Decimal) -> str:
    return f"{amount_atomic / Decimal('1000000'):.6f}"


def decimal_usdc(value: Any, fallback: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return fallback


def dashboard_available_usdc(account: dict[str, Any]) -> float:
    wallet = decimal_usdc(
        account.get("circleUsdcBalance"),
        fallback=atomic_decimal(account.get("availableAtomic")) / Decimal("1000000"),
    )
    gateway = decimal_usdc(
        account.get("gatewayUsdcAvailable"),
        fallback=decimal_usdc(
            account.get("gatewayUsdcTotal"),
            fallback=atomic_decimal(
                account.get("gatewayAvailableAtomic") or account.get("gatewayTotalAtomic")
            )
            / Decimal("1000000"),
        ),
    )
    return float(max(wallet + gateway, Decimal("0")))


def dashboard_withdraw_available_atomic(account: dict[str, Any]) -> str:
    gateway_available_atomic = account.get("gatewayAvailableAtomic")
    if isinstance(gateway_available_atomic, str) and gateway_available_atomic.isdigit():
        return gateway_available_atomic
    gateway_available = account.get("gatewayUsdcAvailable")
    if gateway_available is not None:
        atomic = decimal_usdc_to_atomic_string(gateway_available)
        if atomic is not None:
            return atomic
    available_atomic = account.get("availableAtomic")
    if isinstance(available_atomic, str) and available_atomic.isdigit():
        return available_atomic
    return "0"


def dashboard_transaction(
    entry: dict[str, Any],
    active_gateway_pending_entry_ids: Optional[set[str]] = None,
    active_gateway_pending_deposit_entry_ids: Optional[set[str]] = None,
) -> dict[str, Any]:
    entry_type = str(entry.get("entryType") or "ledger")
    available_delta = atomic_decimal(entry.get("availableDeltaAtomic"))
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    dashboard_status = metadata.get("dashboardStatus")
    base_amount_atomic = dashboard_base_amount_atomic(entry)
    amount_atomic = parse_dashboard_amount_atomic(entry, base_amount_atomic)
    direction = "out" if available_delta < 0 else "in"
    status = "released"
    if "onramp" in entry_type or entry_type == "credit":
        status = "onramp"
    if (
        entry_type == "agent_transfer"
        and active_gateway_pending_entry_ids is not None
        and str(entry.get("entryId") or "") in active_gateway_pending_entry_ids
    ):
        status = "pending_settle"
    elif (
        entry_type == "agent_transfer"
        and str(metadata.get("settlementMode") or "").lower() == "gateway"
        and str(metadata.get("transactionState") or "").upper() not in FINAL_GATEWAY_TRANSFER_STATES
    ):
        status = "pending_settle"
    elif (
        isinstance(dashboard_status, str)
        and dashboard_status.strip()
        and not (entry_type == "agent_transfer" and dashboard_status.strip() == "pending_settle")
    ):
        status = dashboard_status.strip()
    elif entry_type == "pending_settlement":
        status = "pending_settle"
    elif entry_type == "pending_inbound":
        status = "pending_inbound_chain"
    elif entry_type == "withdrawal_submitted":
        status = "withdraw_submitted"
    elif entry_type == "withdrawal":
        status = "withdrawn"
    if (
        status == "credited"
        and entry_type == "credit"
        and active_gateway_pending_deposit_entry_ids is not None
        and str(entry.get("entryId") or "") in active_gateway_pending_deposit_entry_ids
    ):
        status = "pending_inbound_chain"
    withdrawal_lifecycle = status in {"withdraw_submitted", "withdrawn"} or (
        entry_type == "withdrawal_submitted" and status == "failed"
    )
    if withdrawal_lifecycle:
        direction = "out"
    if entry_type == "credit" and status == "credited":
        direction = "in"
    role = "payer" if direction == "out" else "payee"
    if withdrawal_lifecycle:
        role = "withdrawal"
    elif status in {"onramp", "credited", "pending_inbound_chain"} and entry_type == "credit":
        role = "deposit"
    transaction = {
        "id": entry.get("entryId") or "ledger_entry",
        "counterparty": dashboard_counterparty(entry),
        "amount": atomic_to_usdc(amount_atomic),
        "amountAtomic": str(int(amount_atomic)),
        "amountDisplay": dashboard_amount_display(amount_atomic),
        "direction": direction,
        "role": role,
        "status": status,
        "timestamp": entry.get("createdAt") or "ledger",
    }
    for key in (
        "destinationAddress",
        "network",
        "txHash",
        "gasFeeAtomic",
        "gasFee",
        "netAmountAtomic",
        "netAmount",
        "failureReason",
        "linkedEntryId",
        "settlementMode",
        "settlementRecordId",
        "transactionState",
    ):
        value = metadata.get(key)
        if value is not None:
            transaction[key] = value
    return transaction


def active_gateway_pending_entry_ids(
    agent_entries: list[dict[str, Any]],
    current_gateway_pending_batch_atomic: Any,
) -> Optional[set[str]]:
    if current_gateway_pending_batch_atomic is None:
        return None
    remaining = atomic_decimal(current_gateway_pending_batch_atomic)
    active: set[str] = set()
    if remaining <= 0:
        return active
    for entry in agent_entries:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        if entry.get("entryType") != "agent_transfer":
            continue
        if atomic_decimal(entry.get("availableDeltaAtomic")) <= 0:
            continue
        if str(metadata.get("settlementMode") or "").lower() != "gateway":
            continue
        if str(metadata.get("transactionState") or "").upper() not in FINAL_GATEWAY_TRANSFER_STATES:
            continue
        amount = parse_dashboard_amount_atomic(entry, dashboard_base_amount_atomic(entry))
        if amount <= 0 or amount > remaining:
            continue
        entry_id = str(entry.get("entryId") or "")
        if entry_id:
            active.add(entry_id)
            remaining -= amount
    return active


def active_gateway_pending_deposit_entry_ids(
    agent_entries: list[dict[str, Any]],
    current_gateway_pending_deposits_atomic: Any,
) -> Optional[set[str]]:
    if current_gateway_pending_deposits_atomic is None:
        return None
    remaining = atomic_decimal(current_gateway_pending_deposits_atomic)
    active: set[str] = set()
    if remaining <= 0:
        return active
    for entry in agent_entries:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        if entry.get("entryType") != "credit":
            continue
        if metadata.get("dashboardStatus") != "credited":
            continue
        if not metadata.get("linkedEntryId"):
            continue
        amount = parse_dashboard_amount_atomic(entry, dashboard_base_amount_atomic(entry))
        if amount <= 0 or amount > remaining:
            continue
        entry_id = str(entry.get("entryId") or "")
        if entry_id:
            active.add(entry_id)
            remaining -= amount
    return active


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
            "claimedDaysAgo": dashboard_claimed_days_ago(account),
            "ownerEmail": normalize_email(account.get("email")),
        },
        "balance": {
            "available": 0.0,
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
        linked_pending_entry_ids = {
            str(metadata.get("linkedEntryId"))
            for metadata in (
                entry.get("metadata")
                for entry in agent_entries
                if isinstance(entry.get("metadata"), dict)
            )
            if metadata.get("dashboardStatus") == "credited"
            and isinstance(metadata.get("linkedEntryId"), str)
            and metadata.get("linkedEntryId")
        }
        visible_agent_entries = [
            entry
            for entry in agent_entries
            if str(entry.get("entryId") or "") not in linked_pending_entry_ids
        ]
        gateway_pending_batch_atomic = account.get("gatewayPendingBatchAtomic")
        active_pending_entry_ids = active_gateway_pending_entry_ids(
            agent_entries,
            gateway_pending_batch_atomic,
        )
        gateway_pending_deposits_atomic = account.get("gatewayPendingDepositsAtomic")
        active_pending_deposit_entry_ids = active_gateway_pending_deposit_entry_ids(
            agent_entries,
            gateway_pending_deposits_atomic,
        )
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
        pending_settlement_atomic = sum(
            parse_dashboard_amount_atomic(entry, dashboard_base_amount_atomic(entry))
            for entry in agent_entries
            if dashboard_transaction(
                entry,
                active_pending_entry_ids,
                active_pending_deposit_entry_ids,
            )["status"] == "pending_settle"
        )
        wallet_address = (
            account.get("walletAddress")
            or account.get("circleWalletAddress")
            or account.get("circleWalletId")
            or agent_id
        )
        withdraw_available_atomic = dashboard_withdraw_available_atomic(account)
        agents[agent_id] = {
            "agent": {
                "id": agent_id,
                "name": str(account.get("agentName") or agent_id),
                "role": "Agent Wallet Account",
                "walletAddress": short_address(wallet_address),
                "fullWalletAddress": str(wallet_address),
                "claimedDaysAgo": dashboard_claimed_days_ago(account),
                "ownerEmail": normalize_email(account.get("email")),
            },
            "balance": {
                "available": dashboard_available_usdc(account),
                "withdrawAvailable": atomic_to_usdc(withdraw_available_atomic),
                "withdrawAvailableAtomic": withdraw_available_atomic,
                "lifetimeIn": round(lifetime_in, 6),
                "lifetimeOut": round(lifetime_out, 6),
                "pendingSettlement": atomic_to_usdc(pending_settlement_atomic),
                "pendingSettlementAtomic": str(int(pending_settlement_atomic)),
            },
            "transactions": [
                dashboard_transaction(
                    entry,
                    active_pending_entry_ids,
                    active_pending_deposit_entry_ids,
                )
                for entry in visible_agent_entries
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
        state["onrampSessions"] = []
        state["onrampEvents"] = []
        state["circleWebhookEvents"] = []
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
        and any(
            str(entry_id or "").strip() in entry_ids
            for entry_id in record.get("entryIds", [])
        )
    ]
    settlement_records = [
        record
        for record in ledger_state.get("settlementRecords", [])
        if isinstance(record, dict)
        and (
            str(record.get("fromAgentId") or "").strip() == scoped_agent_id
            or str(record.get("toAgentId") or "").strip() == scoped_agent_id
        )
    ]

    state = dict(ledger_state)
    state["accounts"] = accounts
    state["entries"] = entries
    state["onrampSessions"] = onramp_sessions
    state["onrampEvents"] = [
        event
        for event in ledger_state.get("onrampEvents", [])
        if isinstance(event, dict)
        and str(event.get("sessionId") or "").strip() in onramp_session_ids
    ]
    state["circleWebhookEvents"] = [
        event
        for event in ledger_state.get("circleWebhookEvents", [])
        if isinstance(event, dict)
        and str(event.get("agentId") or "").strip() == scoped_agent_id
    ]
    state["chainRecords"] = chain_records
    state["settlementRecords"] = settlement_records
    return state


def build_claimable_agents(
    *,
    email: Optional[str] = None,
    ledger_state: dict[str, Any],
    claimed_agent_ids: list[str],
) -> dict[str, Any]:
    normalized_email = normalize_email(email)
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
        if not agent_id or agent_id in seen:
            continue
        if account.get("dashboardClaimedAt"):
            continue
        account_email = normalize_email(account.get("email"))
        account_type = normalize_wallet_account_type(account.get("accountType"))
        has_circle_wallet = bool(
            str(account.get("walletAddress") or "").strip()
            or str(account.get("circleWalletId") or "").strip()
        )
        if has_circle_wallet and account_type != "EOA":
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
                "ownerEmail": account_email,
                "claimCode": claim_code_for_account(account, account_email or ""),
                "walletAddress": str(wallet_address),
                "displayWalletAddress": short_address(wallet_address),
                "circleWalletId": account.get("circleWalletId"),
                "accountType": account_type,
                "claimStatus": "unclaimed",
                "dashboard": dashboard_agent,
            }
        )
    return {
        "email": normalized_email,
        "agents": candidates,
        "source": "ledger-accounts",
    }
