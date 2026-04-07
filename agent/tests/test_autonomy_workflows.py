import asyncio
import unittest
from typing import Optional

from autonomy_models import RuntimeExecutionRecord, RuntimeIntent
from autonomy_workflows import classify_workflow_failure, execute_trade_workflow


class AutonomyWorkflowTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
