import asyncio
import unittest
from typing import Optional

from autonomy_models import RuntimeExecutionRecord, RuntimeIntent
from autonomy_workflows import (
    classify_workflow_failure,
    execute_chain_workflow,
    execute_trade_workflow,
)


class AutonomyWorkflowTests(unittest.TestCase):
    def test_execute_chain_workflow_confirms_mock_execution(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "chain_submit_execution":
                return {"result": {"txHash": "0xabc123"}}
            if tool_name == "chain_get_wallet_state":
                return {
                    "result": {
                        "wallet": {
                            "signerConfigured": True,
                        }
                    }
                }
            raise AssertionError(f"unexpected tool call: {tool_name}")

        intent = RuntimeIntent(
            intentId="intent-chain-submit_execution",
            intentType="chain",
            action="chain_submit_execution",
            parameters={"operation": "rebalance"},
        )

        execution = asyncio.run(execute_chain_workflow(tool, intent))

        self.assertEqual(
            calls,
            [
                ("chain_submit_execution", {"operation": "rebalance"}),
                ("chain_get_wallet_state", {}),
            ],
        )
        self.assertIsInstance(execution, RuntimeExecutionRecord)
        self.assertEqual(execution.executionId, "exec-intent-chain-submit_execution")
        self.assertEqual(execution.intentId, intent.intentId)
        self.assertEqual(execution.intentType, "chain")
        self.assertEqual(execution.stage, "reconciled")
        self.assertEqual(execution.status, "completed")
        self.assertEqual(execution.externalId, "0xabc123")

    def test_execute_chain_workflow_rejects_non_chain_action(self) -> None:
        async def tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            raise AssertionError(f"unexpected tool call: {tool_name}")

        intent = RuntimeIntent(
            intentId="intent-chain-request_funding",
            intentType="chain",
            action="request_funding",
            parameters={"recommendedFundingUsd": 100},
        )

        with self.assertRaisesRegex(RuntimeError, "Unsupported chain workflow action"):
            asyncio.run(execute_chain_workflow(tool, intent))

    def test_execute_chain_workflow_rejects_unsupported_chain_action(self) -> None:
        async def tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            raise AssertionError(f"unexpected tool call: {tool_name}")

        intent = RuntimeIntent(
            intentId="intent-chain-anything",
            intentType="chain",
            action="chain_anything",
            parameters={"operation": "rebalance"},
        )

        with self.assertRaisesRegex(RuntimeError, "Unsupported chain workflow action"):
            asyncio.run(execute_chain_workflow(tool, intent))

    def test_execute_trade_workflow_confirms_force_exit(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "force_exit_trade":
                return {"result": {"trade_id": "all", "ok": True}}
            if tool_name == "get_budget_snapshot":
                return {"result": {"openTradeCount": 0}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        intent = RuntimeIntent(
            intentId="intent-trade-force_exit_all",
            intentType="trade",
            action="force_exit_all",
        )

        execution = asyncio.run(execute_trade_workflow(tool, intent))

        self.assertEqual(
            calls,
            [
                ("force_exit_trade", {"trade_id": "all", "order_type": "market"}),
                ("get_budget_snapshot", {}),
            ],
        )
        self.assertIsInstance(execution, RuntimeExecutionRecord)
        self.assertEqual(execution.executionId, "exec-intent-trade-force_exit_all")
        self.assertEqual(execution.intentId, intent.intentId)
        self.assertEqual(execution.intentType, "trade")
        self.assertEqual(execution.stage, "reconciled")
        self.assertEqual(execution.status, "completed")
        self.assertEqual(execution.externalId, "all")

    def test_classify_trade_failure_for_rejected_order(self) -> None:
        failure_code = classify_workflow_failure(
            "trade", RuntimeError("order rejected")
        )

        self.assertEqual(failure_code, "trade_order_rejected")

    def test_classify_chain_timeout_failure(self) -> None:
        failure_code = classify_workflow_failure(
            "chain", RuntimeError("confirmation timeout")
        )

        self.assertEqual(failure_code, "chain_confirmation_timeout")


if __name__ == "__main__":
    unittest.main()
