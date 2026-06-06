from __future__ import annotations

import json
import os
import secrets
import uuid
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from fastapi import Cookie, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from payment_router import PaymentIntent, route_payment_intent
import services

from auth import (
    admin_token_matches,
    complete_github_callback,
    complete_google_callback,
    configured_admin_token,
    fetch_github_user,
    sign_admin_session,
    sign_auth_session,
    verify_admin_session,
    verify_auth_session,
)
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
    build_dashboard_data,
    dashboard_base_amount_atomic,
    dashboard_counterparty,
    dashboard_transaction,
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
    enriched_account_payloads,
    ledger_chain_payload,
    ledger_state_with_circle_balances,
    record_ledger_chain_event,
    settle_agent_transfer,
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


app = FastAPI(title="Kovaloop offchain ledger")


def waitlist_cors_origins() -> list[str]:
    configured = os.getenv("WAITLIST_CORS_ORIGINS", "").strip()
    if configured:
        return [
            origin.strip()
            for origin in configured.split(",")
            if origin.strip()
        ]
    return [
        "https://kovaloop.ai",
        "https://www.kovaloop.ai",
        "http://localhost:8080",
        "http://localhost:8000",
        "http://localhost:3000",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:3000",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=waitlist_cors_origins(),
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    max_age=86400,
)
app.mount(
    "/dashboard/assets",
    StaticFiles(directory=LEDGER_DASHBOARD_ASSETS_PATH),
    name="dashboard_assets",
)


@app.middleware("http")
async def add_dashboard_asset_no_cache_headers(
    request: Request,
    call_next: Any,
) -> Response:
    response = await call_next(request)
    if request.url.path.startswith("/dashboard/assets/"):
        response.headers.update(NO_CACHE_HEADERS)
    return response


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
    return {key: value for key, value in metadata.items() if value is not None}


@app.get("/health")
def health() -> dict[str, Any]:
    get_store().load()
    return {"service": "kovaloop-ledger", "status": "ok"}


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


def require_admin_access(
    admin_cookie: str | None,
) -> None:
    if not configured_admin_token():
        raise HTTPException(status_code=403, detail="Admin access is not configured")
    if not verify_admin_session(admin_cookie):
        raise HTTPException(status_code=401, detail="Admin authentication required")


@app.get("/admin")
def ledger_admin(
    request: Request,
    token: str = "",
    admin_cookie: str | None = Cookie(default=None, alias=ADMIN_COOKIE),
) -> Response:
    if not configured_admin_token():
        raise HTTPException(status_code=403, detail="Admin access is not configured")
    if token:
        if not admin_token_matches(token):
            raise HTTPException(status_code=401, detail="Admin authentication required")
        response = RedirectResponse("/admin", status_code=307)
        response.set_cookie(
            ADMIN_COOKIE,
            sign_admin_session(),
            httponly=True,
            samesite="lax",
            secure=public_base_url(request).startswith("https://"),
            max_age=ADMIN_SESSION_MAX_AGE_SECONDS,
        )
        return response
    require_admin_access(admin_cookie)
    return FileResponse(
        LEDGER_CONSOLE_PATH,
        media_type="text/html",
        headers=NO_CACHE_HEADERS,
    )


@app.post("/admin/debug/dashboard-claims/reset")
def debug_reset_dashboard_claims(
    request: DebugResetDashboardClaimsRequest,
    debug_token: str | None = Header(default=None, alias="X-Debug-Token"),
) -> dict[str, Any]:
    expected_token = os.getenv("LEDGER_DEBUG_ADMIN_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(status_code=404, detail="debug admin endpoint is disabled")
    if not debug_token or not secrets.compare_digest(debug_token, expected_token):
        raise HTTPException(status_code=403, detail="invalid debug admin token")
    if request.confirm != "reset-dashboard-claims":
        raise HTTPException(status_code=400, detail="confirm must be reset-dashboard-claims")
    accounts = get_store().reset_dashboard_claims(agent_ids=request.agentIds)
    return {
        "cleared": len(accounts),
        "agentIds": [account.agentId for account in accounts],
    }


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
    if user is None:
        return {"authenticated": False, "user": None}
    owner_email = normalize_email(user.get("email"))
    claimed_agent_ids = []
    if owner_email:
        claimed_agent_ids = get_store().claimed_agent_ids_for_dashboard_email(owner_email)
    return {
        "authenticated": True,
        "user": user,
        "claimedAgentIds": claimed_agent_ids,
    }


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


@app.get("/auth/google/login")
async def google_login(request: Request, returnTo: str = "") -> RedirectResponse:
    try:
        require_env("GOOGLE_CLIENT_ID")
        require_env("GOOGLE_CLIENT_SECRET")
        require_env("AUTH_SESSION_SECRET")
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    state = secrets.token_urlsafe(24)
    redirect_uri = f"{public_base_url(request)}/auth/google/callback"
    query = urlencode(
        {
            "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
        }
    )
    response = RedirectResponse(f"{GOOGLE_AUTHORIZE_URL}?{query}", status_code=307)
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


@app.get("/auth/google/callback")
@app.get("/dashboard/auth/google/callback")
async def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    stored_state: str | None = Cookie(default=None, alias=OAUTH_STATE_COOKIE),
    stored_return: str | None = Cookie(default=None, alias=OAUTH_RETURN_COOKIE),
) -> RedirectResponse:
    return await complete_google_callback(
        request,
        code=code,
        state=state,
        error=error,
        stored_state=stored_state,
        stored_return=stored_return,
        redirect_uri=f"{public_base_url(request)}/auth/google/callback",
    )


@app.post("/auth/logout")
async def auth_logout() -> Response:
    response = Response(content='{"ok":true}', media_type="application/json")
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(OAUTH_STATE_COOKIE)
    response.delete_cookie(OAUTH_RETURN_COOKIE)
    return response


@app.get("/auth/logout")
async def auth_logout_redirect() -> RedirectResponse:
    response = RedirectResponse("/dashboard", status_code=307)
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(OAUTH_STATE_COOKIE)
    response.delete_cookie(OAUTH_RETURN_COOKIE)
    return response


@app.post("/ledger/payment/route")
def route_ledger_payment_intent(intent: PaymentIntent) -> dict[str, Any]:
    return route_payment_intent(intent)


def optional_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def request_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for.strip():
        first = forwarded_for.split(",", 1)[0].strip()
        if first:
            return first
    return request.client.host if request.client else None


@app.post("/waitlist/applications")
def create_waitlist_application(
    application_request: CreateWaitlistApplicationRequest,
    request: Request,
) -> dict[str, Any]:
    email = normalize_email(application_request.email)
    name = optional_text(application_request.name)
    if email is None or name is None:
        raise HTTPException(status_code=400, detail="email and name are required")

    application = WaitlistApplication(
        applicationId=f"waitlist_{uuid.uuid4().hex}",
        email=email,
        name=name,
        company=optional_text(application_request.company),
        intent=optional_text(application_request.intent),
        lang=optional_text(application_request.lang),
        pageUrl=optional_text(application_request.page_url),
        submittedAt=optional_text(application_request.submitted_at),
        clientIp=request_client_ip(request),
        userAgent=optional_text(request.headers.get("user-agent")),
        createdAt=now_iso(),
    )
    saved = get_store().append_waitlist_application(application)
    return {"ok": True, "applicationId": saved.applicationId}


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


def _limit(value: int | None, default: int = 50, maximum: int = 500) -> int:
    if value is None:
        return default
    return max(0, min(value, maximum))


@app.get("/ledger/accounts")
async def list_ledger_accounts(
    ownerEmail: str = "",
    claimedByEmail: str = "",
    claimable: bool = False,
) -> dict[str, Any]:
    accounts = get_store().list_accounts(
        owner_email=ownerEmail,
        claimed_by_email=claimedByEmail,
        claimable=claimable,
    )
    return {"accounts": await enriched_account_payloads(accounts)}


@app.get("/ledger/portfolio")
async def ledger_portfolio(ownerEmail: str = "") -> dict[str, Any]:
    owner_email = normalize_email(ownerEmail)
    if owner_email is None:
        return build_dashboard_data(
            {
                "accounts": [],
                "entries": [],
            },
        )

    store = get_store()
    account_models = store.list_accounts(claimed_by_email=owner_email)
    accounts = await enriched_account_payloads(account_models)
    agent_ids = [
        str(account.get("agentId") or "").strip()
        for account in accounts
        if str(account.get("agentId") or "").strip()
    ]
    entries: list[dict[str, Any]] = []
    for agent_id in agent_ids:
        entries.extend(
            entry.model_dump()
            for entry in store.list_entries(agent_id=agent_id, limit=500)
        )

    return build_dashboard_data(
        {
            "accounts": accounts,
            "entries": entries,
        },
    )


@app.get("/ledger/accounts/{agent_id}/entries")
def list_ledger_account_entries(agent_id: str, limit: int | None = None) -> dict[str, Any]:
    entries = get_store().list_entries(agent_id=agent_id, limit=_limit(limit, default=100))
    return {"entries": [entry.model_dump() for entry in entries]}


@app.get("/ledger/accounts/{agent_id}")
async def get_ledger_account(agent_id: str) -> dict[str, Any]:
    account = get_store().get_account(agent_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    accounts = await enriched_account_payloads([account])
    return {"account": accounts[0]}


@app.get("/ledger/entries")
def list_ledger_entries(
    agentId: str = "",
    type: str = "",
    limit: int | None = 50,
) -> dict[str, Any]:
    entries = get_store().list_entries(
        agent_id=agentId,
        entry_type=type,
        limit=_limit(limit, default=50),
    )
    return {"entries": [entry.model_dump() for entry in entries]}


@app.get("/ledger/onramp-sessions")
def list_ledger_onramp_sessions(agentId: str = "", limit: int | None = 50) -> dict[str, Any]:
    sessions = get_store().list_onramp_sessions(
        agent_id=agentId,
        limit=_limit(limit, default=50),
    )
    return {"onrampSessions": [session.model_dump() for session in sessions]}


@app.get("/ledger/claims/candidates")
async def ledger_claim_candidates() -> dict[str, Any]:
    accounts = await enriched_account_payloads(get_store().list_accounts(claimable=True))
    candidates = []
    for account in accounts:
        account_email = normalize_email(account.get("email"))
        candidates.append(
            {
                "account": account,
                "claimCode": claim_code_for_account(account, account_email or ""),
            }
        )
    return {"candidates": candidates}


@app.post("/ledger/claims")
async def ledger_claim(
    request: DashboardClaimRequest,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any]:
    session_user = verify_auth_session(session_cookie)
    owner_email = normalize_email((session_user or {}).get("email")) or normalize_email(request.email)
    if owner_email is None:
        raise HTTPException(status_code=400, detail="dashboard email is required")
    account_model = get_store().get_account(request.agentId)
    account = account_model.model_dump() if account_model is not None else None
    if account is None:
        raise HTTPException(status_code=404, detail="agent account not found")
    account_email = normalize_email(account.get("email"))
    if account_email is None:
        raise HTTPException(status_code=400, detail="agent account email is required")
    expected_code = claim_code_for_account(account, account_email)
    if not secrets.compare_digest(expected_code, request.claimCode.strip()):
        raise HTTPException(status_code=400, detail="invalid claim code")
    claimed = get_store().claim_dashboard_account(
        agent_id=request.agentId,
        email=account_email,
        dashboard_email=owner_email,
    )
    return {
        "agentId": claimed.agentId,
        "ownerEmail": owner_email,
        "claimed": True,
        "dashboardClaimedAt": claimed.dashboardClaimedAt,
        "dashboardClaimedByEmail": claimed.dashboardClaimedByEmail,
    }


@app.get("/ledger/admin/summary")
async def ledger_admin_summary(
    admin_cookie: str | None = Cookie(default=None, alias=ADMIN_COOKIE),
) -> dict[str, Any]:
    require_admin_access(admin_cookie)
    summary = get_store().admin_summary()
    accounts = await enriched_account_payloads(get_store().list_accounts())
    summary.update(
        {
            "circleUsdcAvailable": str(sum(Decimal(str(account.get("circleUsdcBalance") or "0")) for account in accounts)),
            "gatewayUsdcAvailable": str(sum(Decimal(str(account.get("gatewayUsdcAvailable") or "0")) for account in accounts)),
            "pendingDeposits": str(sum(Decimal(str(account.get("gatewayUsdcPendingDeposits") or "0")) for account in accounts)),
            "pendingBatch": str(sum(Decimal(str(account.get("gatewayUsdcPendingBatch") or "0")) for account in accounts)),
        }
    )
    return summary


@app.get("/ledger/admin/waitlist-applications")
def ledger_admin_waitlist_applications(
    limit: int | None = 100,
    admin_cookie: str | None = Cookie(default=None, alias=ADMIN_COOKIE),
) -> dict[str, Any]:
    require_admin_access(admin_cookie)
    applications = get_store().list_waitlist_applications(
        limit=_limit(limit, default=100),
    )
    return {"applications": [application.model_dump() for application in applications]}


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

        account_model = get_store().get_account(request.agentId)
        synced_account = None
        if account_model is not None:
            synced_account = (await enriched_account_payloads([account_model]))[0]
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


@app.post("/ledger/transfers")
async def transfer_between_agents(request: AgentTransferRequest) -> dict[str, Any]:
    transfer_id = f"transfer_{uuid.uuid4().hex}"
    try:
        sender_account = get_store().get_account(request.fromAgentId)
        receiver_account = get_store().get_account(request.toAgentId)
        if sender_account is None:
            raise LookupError(f"ledger account not found: {request.fromAgentId}")
        if receiver_account is None:
            raise LookupError(f"ledger account not found: {request.toAgentId}")
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
            "fromAgentId": sender_account.agentId,
            "toAgentId": receiver_account.agentId,
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
            entries=entries,
            extra={
                "transferId": transfer_id,
                "fromAgentId": sender_account.agentId,
                "toAgentId": receiver_account.agentId,
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
