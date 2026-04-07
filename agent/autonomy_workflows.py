from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from autonomy_models import RuntimeExecutionRecord, RuntimeIntent


ToolInvoker = Callable[[str, Optional[dict[str, Any]]], Awaitable[dict[str, Any]]]
SUPPORTED_CHAIN_WORKFLOW_ACTIONS = {
    "chain_sign_transfer",
    "chain_submit_execution",
    "chain_submit_user_operation",
}


def _extract_chain_external_id(action: str, result: dict[str, Any]) -> str:
    if action == "chain_sign_transfer":
        transaction = result.get("transaction", {})
        if isinstance(transaction, dict):
            return str(transaction.get("txHash") or "")
        return ""

    settlement = result.get("settlement", {})
    if not isinstance(settlement, dict):
        return ""

    if action == "chain_submit_execution":
        return str(settlement.get("txHash") or "")
    if action == "chain_submit_user_operation":
        return str(settlement.get("userOpHash") or "")
    return ""


def _has_failed_settlement_status(result: dict[str, Any]) -> bool:
    settlement = result.get("settlement", {})
    if not isinstance(settlement, dict):
        return False

    status = settlement.get("status")
    if not isinstance(status, str):
        return False

    normalized_status = status.strip().lower()
    return normalized_status.startswith("fail")


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
    if intent.action not in SUPPORTED_CHAIN_WORKFLOW_ACTIONS:
        raise RuntimeError(f"Unsupported chain workflow action: {intent.action}")

    payload = await tool(intent.action, intent.parameters)
    result = payload["result"]
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected MCP tool result shape: {payload}")

    if _has_failed_settlement_status(result):
        raise RuntimeError("settlement failed")

    external_id = _extract_chain_external_id(intent.action, result)
    if not external_id:
        raise RuntimeError("missing external id")

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
        stage="confirmed",
        status="active",
        externalId=external_id,
    )


def classify_workflow_failure(kind: str, error: Exception) -> str:
    message = str(error).lower()
    if kind == "trade" and "rejected" in message:
        return "trade_order_rejected"
    if kind == "chain" and "timeout" in message:
        return "chain_confirmation_timeout"
    return f"{kind}_workflow_error"
