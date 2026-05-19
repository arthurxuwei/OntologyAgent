from __future__ import annotations

import base64
import os
import secrets
import time
import uuid
from typing import Any, Callable, Literal, Optional
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ed25519
from pydantic import HttpUrl

from payment_router import PaymentIntent, route_payment_intent

DEFAULT_COINBASE_API_BASE_URL = "https://api.developer.coinbase.com"
DEFAULT_COINBASE_TOKEN_PATH = "/onramp/v1/token"
DEFAULT_COINBASE_HOSTED_URL = "https://pay.coinbase.com/buy/select-asset"

_get_store: Optional[Callable[[], Any]] = None


def configure_store_factory(factory: Optional[Callable[[], Any]]) -> None:
    global _get_store
    _get_store = factory


def ledger_store() -> Any:
    if _get_store is None:
        raise RuntimeError("Ledger MCP tools are not configured with a store factory")
    return _get_store()


async def route_payment_intent_tool(
    purpose: str,
    deliveryMode: Literal[
        "funding",
        "agent_transfer",
        "async_task",
        "immediate_api",
        "withdrawal",
        "unknown",
    ] = "unknown",
    requiresAcceptance: bool = False,
    externalService: bool = False,
    serviceUrl: Optional[HttpUrl] = None,
) -> dict[str, Any]:
    return route_payment_intent(
        PaymentIntent(
            purpose=purpose,
            deliveryMode=deliveryMode,
            requiresAcceptance=requiresAcceptance,
            externalService=externalService,
            serviceUrl=serviceUrl,
        )
    )


async def agent_wallet_get_ledger_state_tool() -> dict[str, Any]:
    from main import ledger_state_with_circle_balances

    return await ledger_state_with_circle_balances()


async def agent_wallet_get_or_create_tool(
    agentName: str,
    agentId: str,
    email: Optional[str] = None,
    walletAddress: Optional[str] = None,
    circleWalletId: Optional[str] = None,
    agentDescription: Optional[str] = None,
) -> dict[str, Any]:
    from main import AgentWalletRequest, get_or_create_agent_wallet

    return await get_or_create_agent_wallet(
        AgentWalletRequest(
            agentName=agentName,
            agentId=agentId,
            email=email,
            walletAddress=walletAddress,
            circleWalletId=circleWalletId,
            agentDescription=agentDescription,
        )
    )


def _coinbase_bearer_for(method: str, host: str, path: str) -> str:
    bearer_token = os.getenv("COINBASE_ONRAMP_BEARER_TOKEN")
    if bearer_token:
        return bearer_token
    api_key_id = os.getenv("COINBASE_API_KEY_ID")
    api_private_key = os.getenv("COINBASE_API_PRIVATE_KEY")
    if api_key_id and api_private_key:
        issued_at = int(time.time())
        return jwt.encode(
            {
                "sub": api_key_id,
                "iss": "cdp",
                "nbf": issued_at,
                "exp": issued_at + 120,
                "uri": f"{method.upper()} {host}{path}",
            },
            _ed25519_private_key(api_private_key),
            algorithm="EdDSA",
            headers={"kid": api_key_id, "nonce": secrets.token_hex()},
        )
    if not api_key_id or not api_private_key:
        raise RuntimeError(
            "Coinbase onramp auth is not configured. Set COINBASE_ONRAMP_BEARER_TOKEN "
            "or COINBASE_API_KEY_ID and COINBASE_API_PRIVATE_KEY."
        )


def _ed25519_private_key(api_private_key: str) -> ed25519.Ed25519PrivateKey:
    try:
        raw = base64.b64decode(api_private_key)
    except Exception as error:
        raise RuntimeError("COINBASE_API_PRIVATE_KEY must be base64 encoded") from error
    if len(raw) == 64:
        raw = raw[:32]
    if len(raw) != 32:
        raise RuntimeError("COINBASE_API_PRIVATE_KEY must decode to 32 or 64 bytes")
    return ed25519.Ed25519PrivateKey.from_private_bytes(raw)


async def _create_coinbase_onramp_token(
    *,
    destination_address: str,
    destination_network: str,
    purchase_currency: str,
    client_ip: str,
) -> dict[str, str]:
    if os.getenv("COINBASE_ONRAMP_MOCK", "false").lower() == "true":
        return {
            "token": f"mock-session-token-{uuid.uuid4().hex}",
            "channel_id": f"mock-channel-{uuid.uuid4().hex}",
        }

    api_base_url = os.getenv("COINBASE_ONRAMP_API_BASE_URL", DEFAULT_COINBASE_API_BASE_URL)
    token_path = os.getenv("COINBASE_ONRAMP_TOKEN_PATH", DEFAULT_COINBASE_TOKEN_PATH)
    url = f"{api_base_url.rstrip('/')}{token_path}"
    parsed = httpx.URL(url)
    bearer = _coinbase_bearer_for(
        "POST",
        parsed.host,
        parsed.raw_path.decode("ascii"),
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


def _hosted_onramp_url(
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
    hosted_url = os.getenv("COINBASE_ONRAMP_HOSTED_URL", DEFAULT_COINBASE_HOSTED_URL)
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
    return f"{hosted_url}?{urlencode(query)}"


async def agent_wallet_create_onramp_session_tool(
    agentId: str,
    destinationAddress: str,
    paymentAmount: str,
    idempotencyKey: str,
    clientIp: str = "192.0.2.1",
    destinationNetwork: str = "base",
    purchaseCurrency: str = "USDC",
    paymentCurrency: str = "USD",
    partnerUserRef: Optional[str] = None,
    redirectUrl: Optional[str] = None,
    defaultPaymentMethod: Optional[str] = None,
) -> dict[str, Any]:
    store = ledger_store()
    existing = store.find_onramp_session_by_idempotency_key(idempotencyKey)
    if existing is not None:
        return existing.model_dump()

    token_payload = await _create_coinbase_onramp_token(
        destination_address=destinationAddress,
        destination_network=destinationNetwork,
        purchase_currency=purchaseCurrency,
        client_ip=clientIp,
    )
    session_id = f"onramp_{uuid.uuid4().hex}"
    resolved_partner_user_ref = partnerUserRef or session_id
    from main import OnrampSessionRecord, now_iso

    current = now_iso()
    session = OnrampSessionRecord(
        sessionId=session_id,
        providerToken=token_payload["token"],
        providerChannelId=token_payload.get("channel_id") or None,
        agentId=agentId,
        destinationAddress=destinationAddress,
        destinationNetwork=destinationNetwork,
        purchaseCurrency=purchaseCurrency,
        paymentCurrency=paymentCurrency,
        paymentAmount=paymentAmount,
        clientIp=clientIp,
        partnerUserRef=resolved_partner_user_ref,
        redirectUrl=redirectUrl,
        defaultPaymentMethod=defaultPaymentMethod,
        idempotencyKey=idempotencyKey,
        onrampUrl=_hosted_onramp_url(
            session_token=token_payload["token"],
            partner_user_ref=resolved_partner_user_ref,
            redirect_url=redirectUrl,
            destination_network=destinationNetwork,
            purchase_currency=purchaseCurrency,
            payment_currency=paymentCurrency,
            payment_amount=paymentAmount,
            default_payment_method=defaultPaymentMethod,
        ),
        createdAt=current,
        updatedAt=current,
    )
    return store.add_onramp_session(session).model_dump()


async def agent_wallet_create_escrow_tool(
    buyerAgentId: str,
    sellerAgentId: str,
    amountAtomic: str,
    taskId: Optional[str] = None,
    description: Optional[str] = None,
) -> dict[str, Any]:
    escrow, entry = ledger_store().create_escrow(
        buyer_agent_id=buyerAgentId,
        seller_agent_id=sellerAgentId,
        amount_atomic=amountAtomic,
        task_id=taskId,
        description=description,
        metadata={},
    )
    from main import record_ledger_chain_event

    chain_record = await record_ledger_chain_event(
        event_type="escrow_lock",
        escrow=escrow,
        entries=[entry],
    )
    return {
        "escrow": escrow.model_dump(),
        "entry": entry.model_dump(),
        "chainRecord": chain_record.model_dump() if chain_record is not None else None,
    }


async def agent_wallet_transfer_tool(
    fromEmail: str,
    toEmail: str,
    amountAtomic: str,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    from main import AgentTransferRequest, transfer_between_agents

    return await transfer_between_agents(
        AgentTransferRequest(
            fromEmail=fromEmail,
            toEmail=toEmail,
            amountAtomic=amountAtomic,
            reason=reason,
        )
    )


async def agent_wallet_release_escrow_tool(escrowId: str) -> dict[str, Any]:
    from main import settle_escrow_release

    locked_escrow = ledger_store().get_escrow(escrowId)
    if locked_escrow.status != "locked":
        raise ValueError("escrow is not locked")
    settlement_record = await settle_escrow_release(locked_escrow)
    escrow = ledger_store().release_escrow(escrowId)
    entries = ledger_store().entries_for_escrow_event(
        escrow_id=escrow.escrowId,
        entry_type="escrow_release",
    )
    from main import record_ledger_chain_event

    chain_record = await record_ledger_chain_event(
        event_type="escrow_release",
        escrow=escrow,
        entries=entries,
    )
    return {
        "escrow": escrow.model_dump(),
        "settlementRecord": settlement_record.model_dump()
        if settlement_record is not None
        else None,
        "chainRecord": chain_record.model_dump() if chain_record is not None else None,
    }


async def agent_wallet_refund_escrow_tool(escrowId: str) -> dict[str, Any]:
    escrow = ledger_store().refund_escrow(escrowId)
    entries = ledger_store().entries_for_escrow_event(
        escrow_id=escrow.escrowId,
        entry_type="escrow_refund",
    )
    from main import record_ledger_chain_event

    chain_record = await record_ledger_chain_event(
        event_type="escrow_refund",
        escrow=escrow,
        entries=entries,
    )
    return {
        "escrow": escrow.model_dump(),
        "chainRecord": chain_record.model_dump() if chain_record is not None else None,
    }


def build_mcp_app(store_factory: Callable[[], Any]) -> Any:
    from mcp.server.fastmcp import FastMCP

    configure_store_factory(store_factory)
    mcp = FastMCP(
        "Ledger Skill Provider",
        host="0.0.0.0",
        streamable_http_path="/mcp/",
        stateless_http=True,
        json_response=True,
    )
    mcp.tool(name="route_payment_intent")(route_payment_intent_tool)
    mcp.tool(name="agent_wallet_get_ledger_state")(agent_wallet_get_ledger_state_tool)
    mcp.tool(name="agent_wallet_get_or_create")(agent_wallet_get_or_create_tool)
    mcp.tool(name="agent_wallet_create_onramp_session")(
        agent_wallet_create_onramp_session_tool
    )
    mcp.tool(name="agent_wallet_transfer")(agent_wallet_transfer_tool)
    mcp.tool(name="agent_wallet_create_escrow")(agent_wallet_create_escrow_tool)
    mcp.tool(name="agent_wallet_release_escrow")(agent_wallet_release_escrow_tool)
    mcp.tool(name="agent_wallet_refund_escrow")(agent_wallet_refund_escrow_tool)
    return mcp.streamable_http_app()
