from __future__ import annotations

from typing import Any, Callable, Literal, Optional

from pydantic import HttpUrl

from payment_router import PaymentIntent, route_payment_intent


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
    return ledger_store().load().model_dump()


async def agent_wallet_credit_balance_tool(
    agentId: str,
    amountAtomic: str,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    account, entry = ledger_store().credit(
        agent_id=agentId,
        amount_atomic=amountAtomic,
        reason=reason,
        metadata={},
    )
    return {"account": account.model_dump(), "entry": entry.model_dump()}


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
    return {"escrow": escrow.model_dump(), "entry": entry.model_dump()}


async def agent_wallet_release_escrow_tool(escrowId: str) -> dict[str, Any]:
    escrow = ledger_store().release_escrow(escrowId)
    return {"escrow": escrow.model_dump()}


async def agent_wallet_refund_escrow_tool(escrowId: str) -> dict[str, Any]:
    escrow = ledger_store().refund_escrow(escrowId)
    return {"escrow": escrow.model_dump()}


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
    mcp.tool(name="agent_wallet_credit_balance")(agent_wallet_credit_balance_tool)
    mcp.tool(name="agent_wallet_create_escrow")(agent_wallet_create_escrow_tool)
    mcp.tool(name="agent_wallet_release_escrow")(agent_wallet_release_escrow_tool)
    mcp.tool(name="agent_wallet_refund_escrow")(agent_wallet_refund_escrow_tool)
    return mcp.streamable_http_app()
