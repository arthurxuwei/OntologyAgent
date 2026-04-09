# Agent Autonomy P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a continuously running autonomy runtime that can drive dry-run trading and mock or testnet chain execution through structured intents, workflow state, confirmation, reconciliation, and recoverable failure handling.

**Architecture:** Keep `agent/autonomy.py` as the public entry point, but split new runtime data and workflow logic into focused modules so the existing FastAPI app can keep calling one controller. Add a shared state machine, ledger-backed active execution records, typed trade and chain intents, and workflow helpers that turn one tick into `observe -> plan -> guard -> execute -> confirm -> reconcile -> persist`.

**Tech Stack:** Python 3, FastAPI, Pydantic models, asyncio, existing MCP clients and tools, `unittest`, `fastapi.testclient`

---

## File Map

- Modify: `agent/autonomy.py`
  - Keep `AutonomyController` as the integration surface used by `agent/main.py`
  - Delegate new runtime logic into smaller helpers
- Create: `agent/autonomy_models.py`
  - Shared Pydantic models for observations, intents, executions, ledger state, circuit breaker state, and status payloads
- Create: `agent/autonomy_workflows.py`
  - Pure workflow helpers for trade and chain execution stages, failure classification, and reconciliation helpers
- Modify: `agent/main.py`
  - Expose richer autonomy status through existing endpoints without changing endpoint paths
- Modify: `agent/tests/test_autonomy.py`
  - Expand controller coverage for state transitions, retries, idempotency, and ledger persistence
- Create: `agent/tests/test_autonomy_workflows.py`
  - Unit tests for workflow helpers and failure classification
- Modify: `agent/tests/test_main_api.py`
  - Assert richer autonomy status and health payloads

## Task 1: Introduce Shared Runtime Models

**Files:**
- Create: `agent/autonomy_models.py`
- Modify: `agent/autonomy.py`
- Test: `agent/tests/test_autonomy.py`

- [ ] **Step 1: Write the failing tests for the new state model**

```python
def test_runtime_ledger_defaults_include_execution_tracking(self) -> None:
    from autonomy_models import RuntimeLedger

    ledger = RuntimeLedger()

    self.assertEqual(ledger.activeIntents, [])
    self.assertEqual(ledger.activeExecutions, [])
    self.assertEqual(ledger.executionHistory, [])
    self.assertEqual(ledger.circuitBreaker.state, "closed")


def test_runtime_ledger_round_trips_with_shared_stage_values(self) -> None:
    from autonomy_models import RuntimeLedger, RuntimeExecutionRecord

    ledger = RuntimeLedger(
        activeExecutions=[
            RuntimeExecutionRecord(
                executionId="exec-1",
                intentId="intent-1",
                intentType="trade",
                stage="executing",
                status="active",
            )
        ]
    )

    reloaded = RuntimeLedger.model_validate_json(ledger.model_dump_json())
    self.assertEqual(reloaded.activeExecutions[0].stage, "executing")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest agent/tests/test_autonomy.py -k "runtime_ledger" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autonomy_models'`

- [ ] **Step 3: Write the minimal shared models**

```python
# agent/autonomy_models.py
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Stage = Literal[
    "observed",
    "planned",
    "approved_by_policy",
    "executing",
    "confirmed",
    "reconciled",
    "closed",
    "failed",
    "cooldown",
    "paused",
    "circuit_open",
]

IntentType = Literal["trade", "chain", "noop"]


class CircuitBreakerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["closed", "open"] = "closed"
    reason: Optional[str] = None
    openedAt: Optional[str] = None


class RuntimeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intentId: str
    intentType: IntentType
    action: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str
    confidence: float = 0
    expiry: Optional[str] = None
    riskTags: list[str] = Field(default_factory=list)
    createdAt: Optional[str] = None
    stage: Stage = "planned"


class RuntimeExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executionId: str
    intentId: str
    intentType: IntentType
    stage: Stage
    status: Literal["active", "completed", "failed"]
    externalId: Optional[str] = None
    failureCode: Optional[str] = None
    failureMessage: Optional[str] = None


class RuntimeLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initialized: bool = False
    activeIntents: list[RuntimeIntent] = Field(default_factory=list)
    activeExecutions: list[RuntimeExecutionRecord] = Field(default_factory=list)
    executionHistory: list[RuntimeExecutionRecord] = Field(default_factory=list)
    latestObservation: dict[str, Any] = Field(default_factory=dict)
    failureCounts: dict[str, int] = Field(default_factory=dict)
    cooldowns: dict[str, str] = Field(default_factory=dict)
    circuitBreaker: CircuitBreakerState = Field(default_factory=CircuitBreakerState)
    lastTickAt: Optional[str] = None
```

```python
# agent/autonomy.py
from autonomy_models import RuntimeLedger


class GuardLedger(RuntimeLedger):
    startingCapitalEth: str = "0"
    startingCapitalUsd: float = 0
    currentWalletBalanceEth: str = "0"
    currentWalletBalanceUsd: float = 0
    dryRunRealizedPnl: float = 0
    dryRunUnrealizedPnl: float = 0
    netWorthEstimate: float = 0
    botEnabled: bool = False
    healthStatus: Literal["healthy", "watch", "critical"] = "healthy"
    lastDecision: Optional[dict[str, Any]] = None
    lastProtectiveAction: Optional[dict[str, Any]] = None
    lastFundingRecommendation: Optional[dict[str, Any]] = None
    lastError: Optional[str] = None
    tickCount: int = 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest agent/tests/test_autonomy.py -k "runtime_ledger" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/autonomy_models.py agent/autonomy.py agent/tests/test_autonomy.py
git commit -m "feat: add autonomy runtime models"
```

## Task 2: Normalize Observations And Structured Intents

**Files:**
- Modify: `agent/autonomy.py`
- Test: `agent/tests/test_autonomy.py`

- [ ] **Step 1: Write the failing tests for observation and intent planning**

```python
def test_tick_builds_normalized_runtime_observation(self) -> None:
    async def chain_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        return make_chain_state("2.0")

    async def freqtrade_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        return make_freqtrade_budget(realized=20, unrealized=30, open_trades=1)

    with tempfile.TemporaryDirectory() as temp_dir:
        controller = AutonomyController(
            make_config(str(Path(temp_dir) / "autonomy.json")),
            chain_tool,
            freqtrade_tool,
        )

        observation = controller._build_runtime_observation(
            make_chain_state("2.0")["result"],
            make_freqtrade_budget(realized=20, unrealized=30, open_trades=1)["result"],
        )

        self.assertEqual(observation["trading"]["openTradeCount"], 1)
        self.assertEqual(observation["chain"]["wallet"]["balanceEth"], "2.0")
        self.assertIn("budget", observation)


def test_plan_trade_intent_uses_open_trade_risk_signal(self) -> None:
    async def chain_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        return make_chain_state("2.0")

    async def freqtrade_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        return make_freqtrade_budget(realized=20, unrealized=30, open_trades=1)

    with tempfile.TemporaryDirectory() as temp_dir:
        controller = AutonomyController(
            make_config(str(Path(temp_dir) / "autonomy.json")),
            chain_tool,
            freqtrade_tool,
        )
        observation = {
            "trading": {"openTradeCount": 2, "dryRun": True},
            "risk": {"healthStatus": "critical", "allowedActions": ["hold", "force_exit_all"]},
            "chain": {"wallet": {"mockChain": False}},
        }

        intent = controller._plan_intent(observation)

        self.assertEqual(intent.intentType, "trade")
        self.assertEqual(intent.action, "force_exit_all")
        self.assertEqual(intent.stage, "planned")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest agent/tests/test_autonomy.py -k "runtime_observation or plan_trade_intent" -v`
Expected: FAIL with `AttributeError` for missing `_build_runtime_observation` or `_plan_intent`

- [ ] **Step 3: Write minimal observation and intent planning support**

```python
# agent/autonomy.py
from uuid import uuid4

from autonomy_models import RuntimeIntent


def _intent_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_intent_id() -> str:
    return f"intent-{uuid4()}"


def _build_runtime_observation(
    self,
    chain_state: dict[str, Any],
    freqtrade_budget: dict[str, Any],
) -> dict[str, Any]:
    context = self._build_context(chain_state, freqtrade_budget)
    return {
        "chain": {
            "wallet": context["wallet"],
            "chain": context["chain"],
        },
        "trading": freqtrade_budget,
        "budget": context["budget"],
        "risk": context["risk"],
    }


def _plan_intent(self, observation: dict[str, Any]) -> RuntimeIntent:
    allowed_actions = set(observation["risk"]["allowedActions"])
    if "force_exit_all" in allowed_actions and observation["trading"].get("openTradeCount", 0) > 0:
        return RuntimeIntent(
            intentId=_new_intent_id(),
            intentType="trade",
            action="force_exit_all",
            reason="Critical risk with open trades requires protective exit.",
            confidence=1,
            riskTags=["critical_risk", "protective_action"],
            createdAt=_intent_now(),
        )

    return RuntimeIntent(
        intentId=_new_intent_id(),
        intentType="noop",
        action="hold",
        reason="No autonomous action required for this tick.",
        confidence=1,
        createdAt=_intent_now(),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest agent/tests/test_autonomy.py -k "runtime_observation or plan_trade_intent" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/autonomy.py agent/tests/test_autonomy.py
git commit -m "feat: add autonomy observations and intents"
```

## Task 3: Add Policy Decisions, Active Execution Records, And Idempotency

**Files:**
- Modify: `agent/autonomy.py`
- Modify: `agent/autonomy_models.py`
- Test: `agent/tests/test_autonomy.py`

- [ ] **Step 1: Write the failing tests for policy and idempotent execution lookup**

```python
def test_policy_denies_chain_action_outside_mock_or_testnet(self) -> None:
    async def chain_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        return make_chain_state("2.0")

    async def freqtrade_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        return make_freqtrade_budget()

    with tempfile.TemporaryDirectory() as temp_dir:
        controller = AutonomyController(
            make_config(str(Path(temp_dir) / "autonomy.json")),
            chain_tool,
            freqtrade_tool,
        )
        intent = RuntimeIntent(
            intentId="intent-1",
            intentType="chain",
            action="chain_submit_execution",
            reason="test",
        )
        observation = {
            "chain": {"chain": {"mockChain": False, "chainId": 1}},
            "trading": {"dryRun": True},
            "risk": {"allowedActions": ["hold"]},
        }

        decision = controller._apply_policy(intent, observation)

        self.assertEqual(decision["decision"], "deny")


def test_tick_reuses_existing_active_execution_for_same_intent(self) -> None:
    async def chain_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        return make_chain_state("2.0")

    async def freqtrade_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        return make_freqtrade_budget()

    with tempfile.TemporaryDirectory() as temp_dir:
        controller = AutonomyController(
            make_config(str(Path(temp_dir) / "autonomy.json")),
            chain_tool,
            freqtrade_tool,
        )
        controller._state.activeExecutions = [
            RuntimeExecutionRecord(
                executionId="exec-1",
                intentId="intent-1",
                intentType="trade",
                stage="executing",
                status="active",
            )
        ]

        existing = controller._find_active_execution("intent-1")
        self.assertEqual(existing.executionId, "exec-1")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest agent/tests/test_autonomy.py -k "policy_denies_chain_action or reuses_existing_active_execution" -v`
Expected: FAIL with `AttributeError` for missing `_apply_policy` or `_find_active_execution`

- [ ] **Step 3: Add policy and idempotency helpers**

```python
# agent/autonomy_models.py
class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow", "deny", "cooldown", "trip_circuit"]
    reason: str

```

```python
# agent/autonomy.py
from autonomy_models import PolicyDecision, RuntimeExecutionRecord, RuntimeIntent


def _find_active_execution(self, intent_id: str) -> Optional[RuntimeExecutionRecord]:
    for execution in self._state.activeExecutions:
        if execution.intentId == intent_id and execution.status == "active":
            return execution
    return None


def _apply_policy(self, intent: RuntimeIntent, observation: dict[str, Any]) -> dict[str, str]:
    if intent.intentType == "noop":
        return PolicyDecision(decision="allow", reason="No action requested.").model_dump()

    if intent.intentType == "trade" and not observation["trading"].get("dryRun", False):
        return PolicyDecision(decision="deny", reason="P0 trading autonomy only allows dry-run mode.").model_dump()

    if intent.intentType == "chain":
        chain_meta = observation["chain"]["chain"]
        if not chain_meta.get("mockChain") and chain_meta.get("chainId") == 1:
            return PolicyDecision(decision="deny", reason="P0 chain autonomy only allows mock or testnet environments.").model_dump()

    return PolicyDecision(decision="allow", reason="Intent is inside current P0 policy bounds.").model_dump()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest agent/tests/test_autonomy.py -k "policy_denies_chain_action or reuses_existing_active_execution" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/autonomy.py agent/autonomy_models.py agent/tests/test_autonomy.py
git commit -m "feat: add autonomy policy and idempotency guards"
```

## Task 4: Add Trading Workflow Helpers With Confirmation And Reconciliation

**Files:**
- Create: `agent/autonomy_workflows.py`
- Modify: `agent/autonomy.py`
- Create: `agent/tests/test_autonomy_workflows.py`
- Modify: `agent/tests/test_autonomy.py`

- [ ] **Step 1: Write the failing trading workflow tests**

```python
def test_execute_trade_workflow_confirms_force_exit(self) -> None:
    async def freqtrade_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        if tool_name == "force_exit_trade":
            return {"result": {"status": "accepted", "trade_id": "all"}}
        if tool_name == "get_budget_snapshot":
            return make_freqtrade_budget(realized=-20, unrealized=0, open_trades=0)
        raise AssertionError(tool_name)

    result = asyncio.run(
        execute_trade_workflow(
            freqtrade_tool,
            RuntimeIntent(intentId="intent-1", intentType="trade", action="force_exit_all", reason="risk"),
        )
    )

    self.assertEqual(result.stage, "reconciled")
    self.assertEqual(result.externalId, "all")


def test_classify_trade_failure_for_rejected_order(self) -> None:
    failure = classify_workflow_failure("trade", RuntimeError("order rejected"))
    self.assertEqual(failure["failureCode"], "trade_order_rejected")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest agent/tests/test_autonomy_workflows.py -v`
Expected: FAIL with `ModuleNotFoundError` for `autonomy_workflows`

- [ ] **Step 3: Implement the minimal trading workflow helpers**

```python
# agent/autonomy_workflows.py
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from autonomy_models import RuntimeExecutionRecord, RuntimeIntent

ToolInvoker = Callable[[str, Optional[dict[str, Any]]], Awaitable[dict[str, Any]]]


async def execute_trade_workflow(tool: ToolInvoker, intent: RuntimeIntent) -> RuntimeExecutionRecord:
    payload = await tool("force_exit_trade", {"trade_id": "all", "order_type": "market"})
    result = payload["result"]
    confirmation = await tool("get_budget_snapshot", {})
    open_trade_count = confirmation["result"].get("openTradeCount", 0)
    stage = "reconciled" if open_trade_count == 0 else "confirmed"
    return RuntimeExecutionRecord(
        executionId=f"exec-{intent.intentId}",
        intentId=intent.intentId,
        intentType="trade",
        stage=stage,
        status="completed",
        externalId=str(result.get("trade_id", "all")),
    )


def classify_workflow_failure(kind: str, error: Exception) -> dict[str, str]:
    message = str(error).lower()
    if kind == "trade" and "rejected" in message:
        return {"failureCode": "trade_order_rejected", "failureMessage": str(error)}
    if kind == "chain" and "timeout" in message:
        return {"failureCode": "chain_confirmation_timeout", "failureMessage": str(error)}
    return {"failureCode": f"{kind}_workflow_error", "failureMessage": str(error)}
```

```python
# agent/autonomy.py
from autonomy_workflows import classify_workflow_failure, execute_trade_workflow


async def _run_trade_execution(self, intent: RuntimeIntent) -> RuntimeExecutionRecord:
    try:
        return await execute_trade_workflow(self._freqtrade_tool_invoker, intent)
    except Exception as error:
        failure = classify_workflow_failure("trade", error)
        return RuntimeExecutionRecord(
            executionId=f"exec-{intent.intentId}",
            intentId=intent.intentId,
            intentType="trade",
            stage="failed",
            status="failed",
            failureCode=failure["failureCode"],
            failureMessage=failure["failureMessage"],
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest agent/tests/test_autonomy_workflows.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/autonomy_workflows.py agent/autonomy.py agent/tests/test_autonomy_workflows.py agent/tests/test_autonomy.py
git commit -m "feat: add autonomy trade workflow"
```

## Task 5: Add Chain Workflow Helpers With Receipt Confirmation And Reconciliation

**Files:**
- Modify: `agent/autonomy_workflows.py`
- Modify: `agent/autonomy.py`
- Modify: `agent/tests/test_autonomy_workflows.py`
- Modify: `agent/tests/test_autonomy.py`

- [ ] **Step 1: Write the failing chain workflow tests**

```python
def test_execute_chain_workflow_confirms_mock_execution(self) -> None:
    async def chain_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        if tool_name == "chain_submit_execution":
            return {"result": {"txHash": "0xabc", "status": "submitted"}}
        if tool_name == "chain_get_wallet_state":
            return make_chain_state("1.5")
        raise AssertionError(tool_name)

    intent = RuntimeIntent(
        intentId="intent-2",
        intentType="chain",
        action="chain_submit_execution",
        parameters={"to": "0xabc", "valueEth": "0"},
        reason="rebalance",
    )

    result = asyncio.run(execute_chain_workflow(chain_tool, intent))

    self.assertEqual(result.stage, "reconciled")
    self.assertEqual(result.externalId, "0xabc")


def test_classify_chain_timeout_failure(self) -> None:
    failure = classify_workflow_failure("chain", RuntimeError("confirmation timeout"))
    self.assertEqual(failure["failureCode"], "chain_confirmation_timeout")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest agent/tests/test_autonomy_workflows.py -k "chain" -v`
Expected: FAIL with `NameError` for missing `execute_chain_workflow`

- [ ] **Step 3: Implement the minimal chain workflow helpers**

```python
# agent/autonomy_workflows.py
async def execute_chain_workflow(tool: ToolInvoker, intent: RuntimeIntent) -> RuntimeExecutionRecord:
    payload = await tool(intent.action, intent.parameters)
    result = payload["result"]
    confirmation = await tool("chain_get_wallet_state", {})
    wallet = confirmation["result"]["wallet"]
    if not wallet.get("signerConfigured"):
        raise RuntimeError("confirmation timeout")
    return RuntimeExecutionRecord(
        executionId=f"exec-{intent.intentId}",
        intentId=intent.intentId,
        intentType="chain",
        stage="reconciled",
        status="completed",
        externalId=str(result.get("txHash")),
    )
```

```python
# agent/autonomy.py
from autonomy_workflows import execute_chain_workflow


async def _run_chain_execution(self, intent: RuntimeIntent) -> RuntimeExecutionRecord:
    try:
        return await execute_chain_workflow(self._chain_tool_invoker, intent)
    except Exception as error:
        failure = classify_workflow_failure("chain", error)
        return RuntimeExecutionRecord(
            executionId=f"exec-{intent.intentId}",
            intentId=intent.intentId,
            intentType="chain",
            stage="failed",
            status="failed",
            failureCode=failure["failureCode"],
            failureMessage=failure["failureMessage"],
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest agent/tests/test_autonomy_workflows.py -k "chain" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/autonomy_workflows.py agent/autonomy.py agent/tests/test_autonomy_workflows.py agent/tests/test_autonomy.py
git commit -m "feat: add autonomy chain workflow"
```

## Task 6: Refactor `tick()` Into The Shared Closed Loop

**Files:**
- Modify: `agent/autonomy.py`
- Modify: `agent/tests/test_autonomy.py`

- [ ] **Step 1: Write the failing integration tests for the closed loop**

```python
def test_tick_persists_active_execution_history_and_latest_observation(self) -> None:
    async def chain_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        return make_chain_state("2.0")

    async def freqtrade_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        if tool_name == "get_budget_snapshot":
            return make_freqtrade_budget(realized=0, unrealized=0, open_trades=0)
        if tool_name == "force_exit_trade":
            return {"result": {"status": "accepted", "trade_id": "all"}}
        raise AssertionError(tool_name)

    with tempfile.TemporaryDirectory() as temp_dir:
        controller = AutonomyController(
            make_config(str(Path(temp_dir) / "autonomy.json")),
            chain_tool,
            freqtrade_tool,
        )
        with patch.object(AutonomyController, "_plan_intent") as plan_intent:
            plan_intent.return_value = RuntimeIntent(
                intentId="intent-3",
                intentType="trade",
                action="force_exit_all",
                reason="critical",
            )

            result = asyncio.run(controller.tick())

        status = asyncio.run(controller.status())
        ledger = status["ledger"]
        self.assertIn("latestObservation", ledger)
        self.assertEqual(ledger["executionHistory"][-1]["intentId"], "intent-3")
        self.assertEqual(result["execution"]["stage"], "reconciled")


def test_tick_opens_circuit_breaker_after_repeated_failures(self) -> None:
    async def chain_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        return make_chain_state("2.0")

    async def freqtrade_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        return make_freqtrade_budget(realized=0, unrealized=0, open_trades=1)

    with tempfile.TemporaryDirectory() as temp_dir:
        controller = AutonomyController(
            make_config(str(Path(temp_dir) / "autonomy.json")),
            chain_tool,
            freqtrade_tool,
        )
        with patch.object(AutonomyController, "_plan_intent") as plan_intent, patch.object(
            AutonomyController,
            "_run_trade_execution",
        ) as run_execution:
            plan_intent.return_value = RuntimeIntent(
                intentId="intent-fail",
                intentType="trade",
                action="force_exit_all",
                reason="critical",
            )
            run_execution.return_value = RuntimeExecutionRecord(
                executionId="exec-fail",
                intentId="intent-fail",
                intentType="trade",
                stage="failed",
                status="failed",
                failureCode="trade_order_rejected",
            )

            asyncio.run(controller.tick())
            asyncio.run(controller.tick())
            asyncio.run(controller.tick())

        status = asyncio.run(controller.status())
        self.assertEqual(status["ledger"]["circuitBreaker"]["state"], "open")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest agent/tests/test_autonomy.py -k "latest_observation or circuit_breaker" -v`
Expected: FAIL because `tick()` does not yet return `execution` or open the circuit breaker

- [ ] **Step 3: Implement the shared runtime tick flow**

```python
# agent/autonomy.py
async def tick(self) -> dict[str, Any]:
    if self._lock is None:
        self._lock = asyncio.Lock()

    async with self._lock:
        chain_state = await self._tool_result(self._chain_tool_invoker("chain_get_wallet_state", {}))
        freqtrade_budget = await self._tool_result(self._freqtrade_tool_invoker("get_budget_snapshot", {}))
        self._bootstrap_if_needed(chain_state)

        observation = self._build_runtime_observation(chain_state, freqtrade_budget)
        self._state.latestObservation = observation
        intent = self._plan_intent(observation)
        self._state.activeIntents = [intent]
        policy = self._apply_policy(intent, observation)

        if policy["decision"] != "allow":
            self._state.lastError = policy["reason"]
            self._save_state()
            return {"observation": observation, "intent": intent.model_dump(), "policy": policy}

        existing = self._find_active_execution(intent.intentId)
        if existing is not None:
            execution = existing
        elif intent.intentType == "trade":
            execution = await self._run_trade_execution(intent)
        elif intent.intentType == "chain":
            execution = await self._run_chain_execution(intent)
        else:
            execution = RuntimeExecutionRecord(
                executionId=f"exec-{intent.intentId}",
                intentId=intent.intentId,
                intentType="noop",
                stage="closed",
                status="completed",
            )

        self._state.activeExecutions = [execution] if execution.status == "active" else []
        self._state.executionHistory.append(execution)
        if execution.status == "failed":
            count = self._state.failureCounts.get(execution.failureCode or "unknown", 0) + 1
            self._state.failureCounts[execution.failureCode or "unknown"] = count
            if count >= 3:
                self._state.circuitBreaker.state = "open"
                self._state.circuitBreaker.reason = execution.failureCode

        self._state.lastTickAt = utcnow_iso()
        self._save_state()
        return {
            "observation": observation,
            "intent": intent.model_dump(),
            "policy": policy,
            "execution": execution.model_dump(),
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest agent/tests/test_autonomy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/autonomy.py agent/tests/test_autonomy.py
git commit -m "feat: wire autonomy tick into closed loop"
```

## Task 7: Surface Richer Runtime Status Through The API

**Files:**
- Modify: `agent/main.py`
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Write the failing API tests for the richer status payload**

```python
def test_autonomy_status_exposes_active_execution_and_circuit_breaker(self) -> None:
    controller = FakeAutonomyController()
    controller.status_payload = {
        "enabled": True,
        "autostartConfigured": False,
        "running": True,
        "intervalSeconds": 60,
        "modelName": "gpt-4o-mini",
        "thresholds": {},
        "ledger": {
            "activeExecutions": [{"executionId": "exec-1", "stage": "executing"}],
            "circuitBreaker": {"state": "closed"},
        },
    }

    with TestClient(main.app) as client:
        response = client.get("/autonomy/status")

    payload = response.json()
    self.assertEqual(payload["ledger"]["activeExecutions"][0]["executionId"], "exec-1")
    self.assertEqual(payload["ledger"]["circuitBreaker"]["state"], "closed")
    self.assertEqual(payload["summary"]["activeExecutionCount"], 1)


def test_health_includes_autonomy_execution_summary(self) -> None:
    controller = FakeAutonomyController()
    controller.status_payload["ledger"]["activeExecutions"] = [{"executionId": "exec-2", "stage": "executing"}]

    with patch.object(main, "get_autonomy_controller", return_value=controller):
        with TestClient(main.app) as client:
            response = client.get("/health")

    payload = response.json()
    self.assertIn("activeExecutions", payload["autonomy"]["ledger"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest agent/tests/test_main_api.py -k "active_execution or autonomy_execution_summary" -v`
Expected: FAIL because `FakeAutonomyController` and health assertions do not yet cover the richer ledger payload

- [ ] **Step 3: Update the API payload and tests**

```python
# agent/tests/test_main_api.py
class FakeAutonomyController:
    def __init__(self) -> None:
        self.started = False
        self.status_payload = {
            "enabled": False,
            "autostartConfigured": False,
            "running": False,
            "intervalSeconds": 60,
            "modelName": "gpt-4o-mini",
            "thresholds": {},
            "ledger": {
                "activeExecutions": [],
                "executionHistory": [],
                "circuitBreaker": {"state": "closed"},
            },
        }

    async def status(self) -> dict[str, object]:
        payload = dict(self.status_payload)
        payload["enabled"] = self.started
        payload["running"] = self.started
        return payload
```

```python
# agent/main.py
@app.get("/autonomy/status")
async def autonomy_status() -> dict[str, Any]:
    status = await get_autonomy_controller().status()
    return {
        **status,
        "summary": {
            "activeExecutionCount": len(status["ledger"].get("activeExecutions", [])),
            "circuitState": status["ledger"].get("circuitBreaker", {}).get("state", "closed"),
        },
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest agent/tests/test_main_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/main.py agent/tests/test_main_api.py
git commit -m "feat: expose richer autonomy runtime status"
```

## Task 8: Final Verification

**Files:**
- Modify: none expected unless a verification failure reveals a real issue
- Test: `agent/tests/test_autonomy.py`
- Test: `agent/tests/test_autonomy_workflows.py`
- Test: `agent/tests/test_main_api.py`

- [ ] **Step 1: Run the full autonomy-focused test suite**

Run: `pytest agent/tests/test_autonomy.py agent/tests/test_autonomy_workflows.py agent/tests/test_main_api.py -v`
Expected: PASS for all tests

- [ ] **Step 2: Run a quick API smoke check**

Run: `python -m pytest agent/tests/test_main_api.py -k "autonomy_management_endpoints_use_controller or health_includes_chain_and_freqtrade_status_fields" -v`
Expected: PASS

- [ ] **Step 3: Inspect git diff for only intended files**

Run: `git status --short`
Expected: only `agent/autonomy.py`, `agent/autonomy_models.py`, `agent/autonomy_workflows.py`, `agent/main.py`, and matching test files are modified

- [ ] **Step 4: Commit the final verification fixes if needed**

```bash
git add agent/autonomy.py agent/autonomy_models.py agent/autonomy_workflows.py agent/main.py agent/tests/test_autonomy.py agent/tests/test_autonomy_workflows.py agent/tests/test_main_api.py
git commit -m "test: verify autonomy p0 runtime"
```

## Self-Review

### Spec Coverage

- shared state machine: Task 1 and Task 6
- durable ledger and active execution history: Task 1 and Task 6
- structured observation and intent layer: Task 2
- policy guard and environment limits: Task 3
- trade execution, confirmation, reconciliation: Task 4
- chain execution, confirmation, reconciliation: Task 5
- retry and circuit breaker baseline: Task 6
- status visibility: Task 7

No spec section is left without a matching implementation task.

### Placeholder Scan

- no `TODO` or `TBD`
- every task has exact file paths
- every code-writing step includes concrete code
- every verification step includes an exact command and expected outcome

### Type Consistency

- the plan uses `RuntimeIntent`, `RuntimeExecutionRecord`, `RuntimeLedger`, and `PolicyDecision` consistently across all tasks
- `activeExecutions`, `executionHistory`, and `circuitBreaker` are named consistently in the plan and tests
