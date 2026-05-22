from __future__ import annotations

import json
import os
import secrets
import uuid
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from payment_router import PaymentIntent, route_payment_intent
import services

from auth import complete_github_callback, fetch_github_user, sign_auth_session, verify_auth_session
from clients import (
    CoinbaseAuth,
    CoinbaseOnrampClient,
    LedgerChainRecorder,
    LedgerChainRecordError,
    LedgerSettlementClient,
    LedgerSettlementError,
    LedgerWalletClient,
    encode_ledger_record_payload,
)
from config import *
from dashboard import (
    build_claimable_agents,
    build_dashboard_data,
    dashboard_base_amount_atomic,
    dashboard_counterparty,
    dashboard_transaction,
    empty_dashboard_agent,
    scoped_ledger_state,
)
from models import *
from services import (
    get_chain_recorder,
    get_coinbase_onramp_client,
    get_ledger_settlement_client,
    get_ledger_wallet_client,
    get_or_create_agent_wallet,
    get_store,
    http_error,
    ledger_chain_payload,
    ledger_state_with_circle_balances,
    record_ledger_chain_event,
    settle_agent_transfer,
    settle_escrow_release,
    settle_withdrawal,
)
from store import OffchainLedgerStore, migrate_ledger_state_payload
from utils import *
from webhooks import (
    circle_webhook_completed,
    circle_webhook_event_record,
    circle_webhook_inbound,
    circle_webhook_nested_text,
    circle_webhook_notification,
    circle_webhook_notification_id,
    circle_webhook_signature_required,
    circle_webhook_text,
    circle_webhook_transaction_id,
    circle_webhook_usdc_amount_atomic,
    circle_webhook_wallet_address,
    circle_webhook_wallet_id,
    circle_wallet_status_usdc_amount_atomic,
    gateway_deposit_ledger_metadata,
    process_circle_wallet_webhook,
    verify_circle_webhook_signature,
)


app = FastAPI(title="Chief offchain ledger")
app.mount(
    "/dashboard/assets",
    StaticFiles(directory=LEDGER_DASHBOARD_ASSETS_PATH),
    name="dashboard_assets",
)


FINAL_TRANSFER_STATES = {"SETTLED", "COMPLETE", "COMPLETED", "CONFIRMED"}


def gateway_pending_batch_atomic(action_result: dict[str, Any]) -> int:
    gateway_balance = action_result.get("gatewayBalance")
    if not isinstance(gateway_balance, dict):
        return 0
    value = gateway_balance.get("pendingBatchAtomic")
    if not isinstance(value, str) or not value.isdigit():
        return 0
    return int(value)


def withdrawal_available_basis(
    account: dict[str, Any] | None,
) -> tuple[str | None, str]:
    if isinstance(account, dict):
        gateway_available = account.get("gatewayAvailableAtomic")
        if gateway_available is not None:
            return str(gateway_available), "Gateway available balance"
        available = account.get("availableAtomic")
        if available is not None:
            return str(available), "available balance"
    return None, "available balance"


def agent_transfer_dashboard_metadata(
    settlement_record: LedgerSettlementRecord,
) -> dict[str, Any]:
    action_result = (
        settlement_record.actionResult
        if isinstance(settlement_record.actionResult, dict)
        else {}
    )
    metadata: dict[str, Any] = {
        "settlementMode": settlement_record.mode,
        "transactionState": settlement_record.transactionState,
        "txHash": settlement_record.transactionHash
        or action_result.get("transactionHash")
        or action_result.get("transactionId"),
    }
    pending_batch_atomic = gateway_pending_batch_atomic(action_result)
    if pending_batch_atomic > 0:
        metadata["gatewayPendingBatchAtomic"] = str(pending_batch_atomic)
    state = str(settlement_record.transactionState or "").upper()
    if (
        str(settlement_record.mode or "").lower() == "gateway"
        or pending_batch_atomic > 0
        or (state and state not in FINAL_TRANSFER_STATES)
    ):
        metadata["dashboardStatus"] = "pending_settle"
        metadata["gatewayStage"] = "pending_batch"
    return {key: value for key, value in metadata.items() if value is not None}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"service": "chief-ledger", "status": "ok"}


@app.get("/")
async def ledger_home(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    stored_state: str | None = Cookie(default=None, alias=OAUTH_STATE_COOKIE),
    stored_return: str | None = Cookie(default=None, alias=OAUTH_RETURN_COOKIE),
) -> RedirectResponse:
    if code or error:
        return await complete_github_callback(
            request,
            code=code,
            state=state,
            error=error,
            stored_state=stored_state,
            stored_return=stored_return,
        )
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
async def ledger_dashboard(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    stored_state: str | None = Cookie(default=None, alias=OAUTH_STATE_COOKIE),
    stored_return: str | None = Cookie(default=None, alias=OAUTH_RETURN_COOKIE),
) -> Response:
    if code or error:
        return await complete_github_callback(
            request,
            code=code,
            state=state,
            error=error,
            stored_state=stored_state,
            stored_return=stored_return,
        )
    return FileResponse(
        LEDGER_DASHBOARD_PATH,
        media_type="text/html",
        headers=NO_CACHE_HEADERS,
    )


@app.get("/auth/session")
async def auth_session(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any]:
    user = verify_auth_session(session_cookie)
    return {"authenticated": user is not None, "user": user}


@app.get("/auth/github/login")
async def github_login(request: Request, returnTo: str = "") -> RedirectResponse:
    try:
        require_env("GITHUB_CLIENT_ID")
        require_env("GITHUB_CLIENT_SECRET")
        require_env("AUTH_SESSION_SECRET")
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    state = secrets.token_urlsafe(24)
    query = urlencode(
        {
            "client_id": os.getenv("GITHUB_CLIENT_ID", ""),
            "scope": "read:user user:email",
            "state": state,
        }
    )
    response = RedirectResponse(f"{GITHUB_AUTHORIZE_URL}?{query}", status_code=307)
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        httponly=True,
        samesite="lax",
        secure=public_base_url(request).startswith("https://"),
        max_age=600,
    )
    response.set_cookie(
        OAUTH_RETURN_COOKIE,
        quote(safe_dashboard_return_path(returnTo), safe="/"),
        httponly=True,
        samesite="lax",
        secure=public_base_url(request).startswith("https://"),
        max_age=600,
    )
    return response


@app.get("/auth/github/callback")
@app.get("/dashboard/auth/github/callback")
async def github_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    stored_state: str | None = Cookie(default=None, alias=OAUTH_STATE_COOKIE),
    stored_return: str | None = Cookie(default=None, alias=OAUTH_RETURN_COOKIE),
) -> RedirectResponse:
    return await complete_github_callback(
        request,
        code=code,
        state=state,
        error=error,
        stored_state=stored_state,
        stored_return=stored_return,
    )


@app.post("/auth/logout")
async def auth_logout() -> Response:
    response = Response(content='{"ok":true}', media_type="application/json")
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/ledger/state")
async def get_ledger_state(agentId: str = "") -> dict[str, Any]:
    return scoped_ledger_state(
        await ledger_state_with_circle_balances(),
        agent_id=agentId,
    )


@app.post("/ledger/payment/route")
def route_ledger_payment_intent(intent: PaymentIntent) -> dict[str, Any]:
    return route_payment_intent(intent)


@app.post("/ledger/claims/link")
async def create_claim_link(request: ClaimLinkRequest) -> dict[str, Any]:
    owner_email = normalize_email(request.email)
    if owner_email is None:
        raise HTTPException(status_code=400, detail="email is required")

    try:
        payload = await get_or_create_agent_wallet(
            AgentWalletRequest(
                agentName=request.agentName,
                agentId=request.agentId,
                email=owner_email,
                agentDescription=request.agentDescription,
            )
        )
    except Exception as error:
        raise http_error(error) from error

    account = payload.get("account")
    wallet = payload.get("wallet")
    if not isinstance(account, dict):
        raise HTTPException(status_code=502, detail="claim link response missing account")
    if not isinstance(wallet, dict):
        wallet = {}

    claim_code = claim_code_for_account(account, owner_email)
    response = ClaimLinkResponse(
        agentId=str(account.get("agentId") or request.agentId),
        agentName=str(account.get("agentName") or request.agentName),
        ownerEmail=owner_email,
        claimCode=claim_code,
        claimUrl=dashboard_url({"claimCode": claim_code, "agentId": request.agentId}),
        agentUrl=dashboard_url({"agentId": request.agentId}),
        walletAddress=(
            str(account.get("walletAddress") or wallet.get("walletAddress"))
            if account.get("walletAddress") or wallet.get("walletAddress")
            else None
        ),
        circleWalletId=(
            str(account.get("circleWalletId") or wallet.get("circleWalletId"))
            if account.get("circleWalletId") or wallet.get("circleWalletId")
            else None
        ),
        accountType=normalize_wallet_account_type(
            account.get("accountType") or wallet.get("accountType")
        ),
    )
    return response.model_dump()


@app.get("/dashboard/data")
async def dashboard_data(email: str = "") -> dict[str, Any]:
    return build_dashboard_data(
        await ledger_state_with_circle_balances(),
        owner_email=email,
    )


@app.get("/dashboard/claimable-agents")
async def dashboard_claimable_agents(email: str = "", claimed: str = "") -> dict[str, Any]:
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


@app.head("/circle/webhooks/wallets")
async def circle_wallet_webhook_head() -> Response:
    return Response(status_code=200)


@app.post("/circle/webhooks/wallets")
async def circle_wallet_webhook(request: Request) -> dict[str, Any]:
    body = await request.body()
    await verify_circle_webhook_signature(request, body)
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Circle webhook payload must be JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Circle webhook payload must be an object")
    try:
        return await process_circle_wallet_webhook(payload)
    except (ValueError, RuntimeError) as error:
        raise http_error(error) from error


@app.post("/ledger/gateway/deposits")
async def deposit_agent_wallet_to_gateway(request: GatewayDepositRequest) -> dict[str, Any]:
    try:
        parse_positive_atomic(request.amountAtomic)
        return await services.get_ledger_wallet_client().gateway_deposit(request)
    except (ValueError, RuntimeError) as error:
        raise http_error(error) from error


@app.post("/ledger/gateway/withdrawals")
async def withdraw_agent_wallet_from_gateway(request: GatewayWithdrawalRequest) -> dict[str, Any]:
    try:
        parse_positive_atomic(request.amountAtomic)
        return await services.get_ledger_wallet_client().gateway_withdraw(request)
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
        available_atomic, available_label = withdrawal_available_basis(synced_account)
        get_store().validate_withdrawal(
            agent_id=request.agentId,
            amount_atomic=request.amountAtomic,
            owner_email=request.ownerEmail,
            available_atomic=available_atomic,
            available_label=available_label,
        )
        submitted_entry = get_store().withdrawal_submitted(
            agent_id=request.agentId,
            destination_address=request.destinationAddress,
            amount_atomic=request.amountAtomic,
            reason=request.reason,
            metadata=request.metadata,
            withdrawal_id=withdrawal_id,
        )
        try:
            settlement_record = await settle_withdrawal(
                from_agent_id=request.agentId,
                to_address=request.destinationAddress,
                amount_atomic=request.amountAtomic,
                ref_id=withdrawal_id,
            )
        except LedgerSettlementError as error:
            destination_address = normalize_evm_address(request.destinationAddress)
            _failed_entry = get_store().withdrawal_failed(
                entry_id=submitted_entry.entryId,
                agent_id=request.agentId,
                destination_address=destination_address,
                amount_atomic=request.amountAtomic,
                reason="withdrawal failed",
                metadata=request.metadata,
                withdrawal_id=withdrawal_id,
                failure_reason=error.record.error,
            )
            get_store().add_settlement_record(error.record)
            raise http_error(error) from error
        action_result = (
            settlement_record.actionResult
            if isinstance(settlement_record.actionResult, dict)
            else {}
        )
        withdrawal_metadata = {
            **request.metadata,
            "dashboardStatus": "withdrawn",
            "txHash": settlement_record.transactionHash
            or action_result.get("transactionHash"),
            "gasFeeAtomic": action_result.get("estimatedGasFeeAtomic"),
            "gasFee": action_result.get("estimatedGasFee"),
            "netAmountAtomic": action_result.get("netAmountAtomic"),
            "netAmount": action_result.get("netAmount"),
            "network": "Base",
        }
        account, entry = get_store().withdrawal_completed(
            entry_id=submitted_entry.entryId,
            agent_id=request.agentId,
            destination_address=request.destinationAddress,
            amount_atomic=request.amountAtomic,
            reason=request.reason,
            metadata=withdrawal_metadata,
            withdrawal_id=withdrawal_id,
            settlement_record_id=settlement_record.recordId,
            available_atomic=available_atomic,
            available_label=available_label,
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
        "entries": [entry.model_dump()],
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
            **agent_transfer_dashboard_metadata(settlement_record),
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
