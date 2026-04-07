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
    def test_execute_chain_workflow_uses_supported_external_id_shapes(self) -> None:
        scenarios = [
            (
                "chain_sign_transfer",
                {"to": "0xabc", "amountEth": "0.1"},
                {"result": {"transaction": {"txHash": "0xsign123"}}},
                "0xsign123",
            ),
            (
                "chain_submit_execution",
                {"operation": "rebalance"},
                {"result": {"settlement": {"txHash": "0xexec123"}}},
                "0xexec123",
            ),
            (
                "chain_submit_user_operation",
                {"target": "0xdef"},
                {"result": {"settlement": {"userOpHash": "0xuserop123"}}},
                "0xuserop123",
            ),
        ]

        for action, parameters, workflow_result, expected_external_id in scenarios:
            with self.subTest(action=action):
                calls: list[tuple[str, dict[str, object]]] = []

                async def tool(
                    tool_name: str, arguments: Optional[dict[str, object]] = None
                ) -> dict[str, object]:
                    calls.append((tool_name, arguments or {}))
                    if tool_name == action:
                        return workflow_result
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
                    intentId=f"intent-{action}",
                    intentType="chain",
                    action=action,
                    parameters=parameters,
                )

                execution = asyncio.run(execute_chain_workflow(tool, intent))

                self.assertEqual(
                    calls,
                    [
                        (action, parameters),
                        ("chain_get_wallet_state", {}),
                    ],
                )
                self.assertIsInstance(execution, RuntimeExecutionRecord)
                self.assertEqual(execution.executionId, f"exec-intent-{action}")
                self.assertEqual(execution.intentId, intent.intentId)
                self.assertEqual(execution.intentType, "chain")
                self.assertEqual(execution.stage, "confirmed")
                self.assertEqual(execution.status, "active")
                self.assertEqual(execution.externalId, expected_external_id)

    def test_execute_chain_workflow_keeps_submission_active_without_external_id_confirmation(
        self,
    ) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "chain_submit_execution":
                return {
                    "result": {
                        "settlement": {"txHash": "0xexec123", "status": "submitted"}
                    }
                }
            if tool_name == "chain_get_wallet_state":
                return {"result": {"wallet": {"signerConfigured": True}}}
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
        self.assertEqual(execution.stage, "confirmed")
        self.assertEqual(execution.status, "active")
        self.assertEqual(execution.externalId, "0xexec123")

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

    def test_execute_chain_workflow_rejects_failed_settlement_status(self) -> None:
        async def tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            if tool_name == "chain_submit_execution":
                return {
                    "result": {
                        "settlement": {"txHash": "0xexec123", "status": "failed"}
                    }
                }
            if tool_name == "chain_get_wallet_state":
                return {"result": {"wallet": {"signerConfigured": True}}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        intent = RuntimeIntent(
            intentId="intent-chain-submit_execution",
            intentType="chain",
            action="chain_submit_execution",
            parameters={"operation": "rebalance"},
        )

        with self.assertRaisesRegex(RuntimeError, "failed"):
            asyncio.run(execute_chain_workflow(tool, intent))

    def test_execute_chain_workflow_rejects_missing_settlement_hash(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "chain_submit_user_operation":
                return {
                    "result": {"settlement": {"status": "submitted", "userOpHash": ""}}
                }
            if tool_name == "chain_get_wallet_state":
                return {"result": {"wallet": {"signerConfigured": True}}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        intent = RuntimeIntent(
            intentId="intent-chain-submit_user_operation",
            intentType="chain",
            action="chain_submit_user_operation",
            parameters={"target": "0xdef"},
        )

        with self.assertRaisesRegex(RuntimeError, "missing external id"):
            asyncio.run(execute_chain_workflow(tool, intent))

        self.assertEqual(
            calls,
            [("chain_submit_user_operation", {"target": "0xdef"})],
        )

    def test_execute_chain_workflow_rejects_missing_transfer_hash(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "chain_sign_transfer":
                return {"result": {"transaction": {"txHash": ""}}}
            if tool_name == "chain_get_wallet_state":
                return {"result": {"wallet": {"signerConfigured": True}}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        intent = RuntimeIntent(
            intentId="intent-chain-sign_transfer",
            intentType="chain",
            action="chain_sign_transfer",
            parameters={"to": "0xabc", "amountEth": "0.1"},
        )

        with self.assertRaisesRegex(RuntimeError, "missing external id"):
            asyncio.run(execute_chain_workflow(tool, intent))

        self.assertEqual(
            calls,
            [("chain_sign_transfer", {"to": "0xabc", "amountEth": "0.1"})],
        )

    def test_execute_chain_workflow_rejects_missing_execution_hash(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "chain_submit_execution":
                return {"result": {"settlement": {"status": "submitted", "txHash": ""}}}
            if tool_name == "chain_get_wallet_state":
                return {"result": {"wallet": {"signerConfigured": True}}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        intent = RuntimeIntent(
            intentId="intent-chain-submit_execution",
            intentType="chain",
            action="chain_submit_execution",
            parameters={"operation": "rebalance"},
        )

        with self.assertRaisesRegex(RuntimeError, "missing external id"):
            asyncio.run(execute_chain_workflow(tool, intent))

        self.assertEqual(
            calls,
            [("chain_submit_execution", {"operation": "rebalance"})],
        )

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
