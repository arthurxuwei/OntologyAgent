from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from autonomy_models import RuntimeExecutionRecord, RuntimeIntent


ToolInvoker = Callable[[str, Optional[dict[str, Any]]], Awaitable[dict[str, Any]]]


async def execute_trade_workflow(
    tool: ToolInvoker,
    intent: RuntimeIntent,
) -> RuntimeExecutionRecord:
    payload = await tool(
        "force_exit_trade",
        {"trade_id": "all", "order_type": "market"},
    )
    result = payload["result"]
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected MCP tool result shape: {payload}")

    confirmation_payload = await tool("get_budget_snapshot", {})
    confirmation = confirmation_payload["result"]
    if not isinstance(confirmation, dict):
        raise RuntimeError(f"Unexpected MCP tool result shape: {confirmation_payload}")

    external_id = str(result.get("trade_id") or "all")
    stage = (
        "reconciled" if int(confirmation.get("openTradeCount", 0)) == 0 else "confirmed"
    )

    return RuntimeExecutionRecord(
        executionId=f"exec-{intent.intentId}",
        intentId=intent.intentId,
        intentType="trade",
        stage=stage,
        status="completed",
        externalId=external_id,
    )


async def execute_chain_workflow(
    tool: ToolInvoker,
    intent: RuntimeIntent,
) -> RuntimeExecutionRecord:
    payload = await tool(intent.action, intent.parameters)
    result = payload["result"]
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected MCP tool result shape: {payload}")

    confirmation_payload = await tool("chain_get_wallet_state", {})
    confirmation = confirmation_payload["result"]
    if not isinstance(confirmation, dict):
        raise RuntimeError(f"Unexpected MCP tool result shape: {confirmation_payload}")

    wallet = confirmation.get("wallet", {})
    if not isinstance(wallet, dict) or not wallet.get("signerConfigured"):
        raise RuntimeError("confirmation timeout")

    return RuntimeExecutionRecord(
        executionId=f"exec-{intent.intentId}",
        intentId=intent.intentId,
        intentType="chain",
        stage="reconciled",
        status="completed",
        externalId=str(result.get("txHash") or ""),
    )


def classify_workflow_failure(kind: str, error: Exception) -> str:
    message = str(error).lower()
    if kind == "trade" and "rejected" in message:
        return "trade_order_rejected"
    if kind == "chain" and "timeout" in message:
        return "chain_confirmation_timeout"
    return f"{kind}_workflow_error"
