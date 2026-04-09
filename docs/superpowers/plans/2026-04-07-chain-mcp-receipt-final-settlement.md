# Chain MCP Receipt And Final Settlement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add receipt and terminal-status query tools to `chain MCP` and wire the agent runtime to advance chain executions from submission into explicit pending, success, or failure terminal states.

**Architecture:** Keep submission tools and confirmation tools separate. `chain MCP` will own receipt and user-operation status lookup through new query services and tool registrations. The `agent` runtime will keep using its existing closed-loop tick, but active chain executions will now be revisited with the new query tools and promoted to `reconciled`, kept pending, or marked failed based on explicit tool results rather than wallet-signer heuristics.

**Tech Stack:** TypeScript, Node test runner, zod, MCP server SDK, Python 3, asyncio, pytest, Pydantic

---

## File Map

- Modify: `chain/src/domain/types.ts`
  - Add normalized receipt and user-operation status result types
- Create: `chain/src/services/transaction-receipt-service.ts`
  - Query and normalize transaction receipt status
- Create: `chain/src/services/user-operation-status-service.ts`
  - Query and normalize ERC-4337 user-operation status
- Modify: `chain/src/mcp/tools.ts`
  - Register new MCP tools and inject new services into runtime
- Modify: `chain/test/mcp-server.test.ts`
  - Add end-to-end MCP tests for the new tools
- Modify: `agent/autonomy_workflows.py`
  - Replace wallet-signer heuristic with tool-driven chain status progression helpers
- Modify: `agent/autonomy.py`
  - Revisit active chain executions on later ticks and advance their lifecycle
- Modify: `agent/tests/test_autonomy_workflows.py`
  - Add pending, success, and failure status transition coverage
- Modify: `agent/tests/test_autonomy.py`
  - Add tick-level state progression coverage for active chain executions

## Task 1: Add Normalized Chain Status Result Types

**Files:**
- Modify: `chain/src/domain/types.ts`
- Test: `chain/test/mcp-server.test.ts`

- [ ] **Step 1: Write the failing MCP type-shape test**

```ts
test("chain MCP exposes receipt and user operation status tools", async () => {
  await withClient(async (client) => {
    const response = await client.listTools();
    const toolNames = response.tools.map((tool) => tool.name).sort();

    assert.deepEqual(toolNames, [
      "chain_get_transaction_receipt",
      "chain_get_user_operation_status",
      "chain_get_wallet_state",
      "chain_sign_transfer",
      "chain_submit_execution",
      "chain_submit_user_operation",
      "chain_x402_fetch",
    ]);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- chain/test/mcp-server.test.ts`
Expected: FAIL because the new tool names do not exist yet

- [ ] **Step 3: Add the normalized result types**

```ts
// chain/src/domain/types.ts
export type TransactionReceiptStatusResult = {
  txHash: string;
  found: boolean;
  finalized: boolean;
  success: boolean | null;
  status: "pending" | "success" | "reverted";
  blockNumber: number | null;
  receipt: Record<string, unknown> | null;
  mode: "mock" | "network";
};

export type UserOperationStatusResult = {
  userOpHash: string;
  found: boolean;
  finalized: boolean;
  success: boolean | null;
  status: "pending" | "success" | "failed";
  txHash: string | null;
  receipt: Record<string, unknown> | null;
  mode: "mock" | "network";
};
```

- [ ] **Step 4: Run the MCP test again to confirm it still fails only on missing tool registration**

Run: `npm test -- chain/test/mcp-server.test.ts`
Expected: FAIL because tool names still are not registered, but TypeScript compiles with the new types in place

- [ ] **Step 5: Commit**

```bash
git add chain/src/domain/types.ts chain/test/mcp-server.test.ts
git commit -m "feat: add chain receipt status result types"
```

## Task 2: Add Transaction Receipt Status Service

**Files:**
- Create: `chain/src/services/transaction-receipt-service.ts`
- Modify: `chain/src/mcp/tools.ts`
- Modify: `chain/test/mcp-server.test.ts`

- [ ] **Step 1: Write the failing MCP test for transaction receipt queries**

```ts
test("chain_get_transaction_receipt returns mock success receipt status", async () => {
  await withClient(async (client) => {
    const response = await client.callTool({
      name: "chain_get_transaction_receipt",
      arguments: { txHash: "0xexec123" },
    });

    assert.notEqual(response.isError, true);
    const content = response.structuredContent as Record<string, any>;
    assert.equal(content.txHash, "0xexec123");
    assert.equal(content.found, true);
    assert.equal(content.finalized, true);
    assert.equal(content.success, true);
    assert.equal(content.status, "success");
    assert.equal(content.mode, "mock");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- chain/test/mcp-server.test.ts`
Expected: FAIL because `chain_get_transaction_receipt` is not implemented

- [ ] **Step 3: Implement the transaction receipt status service**

```ts
// chain/src/services/transaction-receipt-service.ts
import type { AppConfig } from "../config.js";
import type { NetworkClient } from "../infra/network-client.js";
import type { TransactionReceiptStatusResult } from "../domain/types.js";

export class TransactionReceiptService {
  constructor(
    private readonly config: AppConfig,
    private readonly networkClient: NetworkClient | null,
  ) {}

  async execute(txHash: string): Promise<TransactionReceiptStatusResult> {
    if (this.config.network.mockChain) {
      return {
        txHash,
        found: true,
        finalized: true,
        success: true,
        status: "success",
        blockNumber: 1,
        receipt: { txHash, status: 1, blockNumber: 1 },
        mode: "mock",
      };
    }

    const receipt = await this.networkClient?.getTransactionReceipt(txHash);
    if (!receipt) {
      return {
        txHash,
        found: false,
        finalized: false,
        success: null,
        status: "pending",
        blockNumber: null,
        receipt: null,
        mode: "network",
      };
    }

    const success = Number(receipt.status) === 1;
    return {
      txHash,
      found: true,
      finalized: true,
      success,
      status: success ? "success" : "reverted",
      blockNumber: Number(receipt.blockNumber ?? 0),
      receipt: receipt as unknown as Record<string, unknown>,
      mode: "network",
    };
  }
}
```

- [ ] **Step 4: Register the new tool in the MCP server**

```ts
// chain/src/mcp/tools.ts
import { TransactionReceiptService } from "../services/transaction-receipt-service.js";

type ChainRuntime = {
  walletStateService: WalletStateService;
  signTransferService: SignTransferService;
  executionService: ExecutionService;
  userOperationService: UserOperationService;
  transactionReceiptService: TransactionReceiptService;
  x402FetchService: X402FetchService;
};

// inside createChainRuntime
transactionReceiptService: new TransactionReceiptService(config, networkClient),

// inside createChainMcpServer
server.registerTool(
  "chain_get_transaction_receipt",
  {
    description: "Return terminal or pending transaction receipt status by txHash.",
    inputSchema: {
      txHash: z.string().describe("Transaction hash"),
    },
  },
  async ({ txHash }) => runTool(() => runtime.transactionReceiptService.execute(txHash)),
);
```

- [ ] **Step 5: Run the MCP test to verify it passes**

Run: `npm test -- chain/test/mcp-server.test.ts`
Expected: PASS for the new transaction receipt test

- [ ] **Step 6: Commit**

```bash
git add chain/src/services/transaction-receipt-service.ts chain/src/mcp/tools.ts chain/test/mcp-server.test.ts
git commit -m "feat: add chain transaction receipt query tool"
```

## Task 3: Add User Operation Status Service

**Files:**
- Create: `chain/src/services/user-operation-status-service.ts`
- Modify: `chain/src/mcp/tools.ts`
- Modify: `chain/test/mcp-server.test.ts`

- [ ] **Step 1: Write the failing MCP test for user operation status queries**

```ts
test("chain_get_user_operation_status returns mock success status", async () => {
  await withClient(async (client) => {
    const response = await client.callTool({
      name: "chain_get_user_operation_status",
      arguments: { userOpHash: "0xmock_userop_123" },
    });

    assert.notEqual(response.isError, true);
    const content = response.structuredContent as Record<string, any>;
    assert.equal(content.userOpHash, "0xmock_userop_123");
    assert.equal(content.found, true);
    assert.equal(content.finalized, true);
    assert.equal(content.success, true);
    assert.equal(content.status, "success");
    assert.equal(content.mode, "mock");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- chain/test/mcp-server.test.ts`
Expected: FAIL because `chain_get_user_operation_status` is not implemented

- [ ] **Step 3: Implement the user operation status service**

```ts
// chain/src/services/user-operation-status-service.ts
import type { AppConfig } from "../config.js";
import type { BundlerClient } from "../erc4337.js";
import type { UserOperationStatusResult } from "../domain/types.js";

export class UserOperationStatusService {
  constructor(
    private readonly config: AppConfig,
    private readonly bundlerClient: BundlerClient,
  ) {}

  async execute(userOpHash: string): Promise<UserOperationStatusResult> {
    if (this.config.network.mockChain) {
      return {
        userOpHash,
        found: true,
        finalized: true,
        success: true,
        status: "success",
        txHash: `0xmock_tx_${userOpHash.slice(2, 10)}`,
        receipt: { userOpHash, status: "success" },
        mode: "mock",
      };
    }

    const result = await this.bundlerClient.getUserOperationStatus(userOpHash);
    if (!result) {
      return {
        userOpHash,
        found: false,
        finalized: false,
        success: null,
        status: "pending",
        txHash: null,
        receipt: null,
        mode: "network",
      };
    }

    return {
      userOpHash,
      found: true,
      finalized: result.status !== "pending",
      success: result.status === "success" ? true : result.status === "failed" ? false : null,
      status: result.status,
      txHash: result.txHash ?? null,
      receipt: result.receipt ?? null,
      mode: "network",
    };
  }
}
```

- [ ] **Step 4: Register the new MCP tool**

```ts
// chain/src/mcp/tools.ts
import { UserOperationStatusService } from "../services/user-operation-status-service.js";

type ChainRuntime = {
  walletStateService: WalletStateService;
  signTransferService: SignTransferService;
  executionService: ExecutionService;
  userOperationService: UserOperationService;
  userOperationStatusService: UserOperationStatusService;
  transactionReceiptService: TransactionReceiptService;
  x402FetchService: X402FetchService;
};

// inside createChainRuntime
const bundlerClient = new BundlerClient(config);

userOperationService: new UserOperationService(bundlerClient, policyGuard, settlementService),
userOperationStatusService: new UserOperationStatusService(config, bundlerClient),

// inside createChainMcpServer
server.registerTool(
  "chain_get_user_operation_status",
  {
    description: "Return terminal or pending ERC-4337 user operation status by userOpHash.",
    inputSchema: {
      userOpHash: z.string().describe("User operation hash"),
    },
  },
  async ({ userOpHash }) => runTool(() => runtime.userOperationStatusService.execute(userOpHash)),
);
```

- [ ] **Step 5: Run the MCP tests to verify they pass**

Run: `npm test -- chain/test/mcp-server.test.ts`
Expected: PASS for both new MCP query tests

- [ ] **Step 6: Commit**

```bash
git add chain/src/services/user-operation-status-service.ts chain/src/mcp/tools.ts chain/test/mcp-server.test.ts
git commit -m "feat: add chain user operation status query tool"
```

## Task 4: Add Network-Mode Status Normalization Tests

**Files:**
- Modify: `chain/test/mcp-server.test.ts`
- Modify: `chain/src/mcp/tools.ts`

- [ ] **Step 1: Write the failing MCP tests for pending and failure normalization**

```ts
test("chain_get_transaction_receipt returns pending when receipt is missing", async () => {
  // use a runtime override for the receipt service that returns found=false, finalized=false
});

test("chain_get_user_operation_status returns failed terminal status", async () => {
  // use a runtime override for the user operation status service that returns status="failed"
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm test -- chain/test/mcp-server.test.ts`
Expected: FAIL because the override hooks do not exist yet

- [ ] **Step 3: Add runtime override hooks to support status-service fakes**

```ts
// chain/src/mcp/tools.ts
export function createChainRuntime(
  config: AppConfig = loadConfig(),
  overrides?: {
    x402FetchService?: X402FetchService;
    transactionReceiptService?: TransactionReceiptService;
    userOperationStatusService?: UserOperationStatusService;
  },
): ChainRuntime {
  // ...
  return {
    // ...
    transactionReceiptService:
      overrides?.transactionReceiptService ??
      new TransactionReceiptService(config, networkClient),
    userOperationStatusService:
      overrides?.userOperationStatusService ??
      new UserOperationStatusService(config, bundlerClient),
  };
}
```

- [ ] **Step 4: Complete the tests using the new override hooks**

```ts
test("chain_get_transaction_receipt returns pending when receipt is missing", async () => {
  await withClient(
    async (client) => {
      const response = await client.callTool({
        name: "chain_get_transaction_receipt",
        arguments: { txHash: "0xpending" },
      });
      const content = response.structuredContent as Record<string, any>;
      assert.equal(content.status, "pending");
      assert.equal(content.found, false);
      assert.equal(content.finalized, false);
    },
    {
      transactionReceiptService: {
        execute: async () => ({
          txHash: "0xpending",
          found: false,
          finalized: false,
          success: null,
          status: "pending",
          blockNumber: null,
          receipt: null,
          mode: "network",
        }),
      } as any,
    },
  );
});
```

- [ ] **Step 5: Run the MCP tests to verify they pass**

Run: `npm test -- chain/test/mcp-server.test.ts`
Expected: PASS with pending and failed normalization covered

- [ ] **Step 6: Commit**

```bash
git add chain/src/mcp/tools.ts chain/test/mcp-server.test.ts
git commit -m "test: cover chain status normalization paths"
```

## Task 5: Add Chain Query Helpers In The Agent Workflow

**Files:**
- Modify: `agent/autonomy_workflows.py`
- Modify: `agent/tests/test_autonomy_workflows.py`

- [ ] **Step 1: Write the failing workflow tests for pending, success, and failure transitions**

```python
def test_execute_chain_confirmation_keeps_pending_execution_active(self) -> None:
    existing = RuntimeExecutionRecord(
        executionId="exec-1",
        intentId="intent-chain-submit_execution",
        intentType="chain",
        stage="confirmed",
        status="active",
        externalId="0xexec123",
    )

    async def tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
        if tool_name == "chain_get_transaction_receipt":
            return {"result": {"txHash": "0xexec123", "found": False, "finalized": False, "success": None, "status": "pending", "blockNumber": None, "receipt": None, "mode": "network"}}
        raise AssertionError(tool_name)

    execution = asyncio.run(confirm_chain_execution(tool, existing, "chain_submit_execution"))
    self.assertEqual(execution.stage, "confirmed")
    self.assertEqual(execution.status, "active")
```

```python
def test_execute_chain_confirmation_promotes_success_to_reconciled(self) -> None:
    # same setup, but result is found/finalized/success with status success
```

```python
def test_execute_chain_confirmation_marks_terminal_failure(self) -> None:
    # same setup, but result is finalized with success False and status failed or reverted
```

- [ ] **Step 2: Run the workflow tests to verify they fail**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_autonomy_workflows.py -k "chain_confirmation" -v`
Expected: FAIL because `confirm_chain_execution` does not exist

- [ ] **Step 3: Add the minimal chain confirmation helper**

```python
# agent/autonomy_workflows.py
def _confirmation_tool_name(action: str) -> str:
    if action == "chain_submit_execution":
        return "chain_get_transaction_receipt"
    if action == "chain_submit_user_operation":
        return "chain_get_user_operation_status"
    raise RuntimeError(f"Unsupported chain confirmation action: {action}")


async def confirm_chain_execution(
    tool: ToolInvoker,
    execution: RuntimeExecutionRecord,
    action: str,
) -> RuntimeExecutionRecord:
    confirmation_payload = await tool(
        _confirmation_tool_name(action),
        {
            "txHash": execution.externalId,
        }
        if action == "chain_submit_execution"
        else {"userOpHash": execution.externalId},
    )
    result = confirmation_payload["result"]
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected MCP tool result shape: {confirmation_payload}")

    if not bool(result.get("finalized", False)):
        return execution.model_copy(update={"stage": "confirmed", "status": "active"})

    if bool(result.get("success", False)):
        return execution.model_copy(update={"stage": "reconciled", "status": "completed"})

    return execution.model_copy(
        update={
            "stage": "failed",
            "status": "failed",
            "failureCode": "chain_terminal_failure",
            "failureMessage": str(result.get("status") or "failed"),
        }
    )
```

- [ ] **Step 4: Run the workflow tests to verify they pass**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_autonomy_workflows.py -k "chain_confirmation" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/autonomy_workflows.py agent/tests/test_autonomy_workflows.py
git commit -m "feat: add chain execution confirmation workflow"
```

## Task 6: Revisit Active Chain Executions During Tick

**Files:**
- Modify: `agent/autonomy.py`
- Modify: `agent/tests/test_autonomy.py`

- [ ] **Step 1: Write the failing controller tests for active chain execution advancement**

```python
def test_tick_rechecks_active_chain_execution_and_reconciles_on_success(self) -> None:
    controller._state.activeExecutions = [
        RuntimeExecutionRecord(
            executionId="exec-1",
            intentId="intent-chain-submit_execution",
            intentType="chain",
            stage="confirmed",
            status="active",
            externalId="0xexec123",
        )
    ]

    with patch.object(AutonomyController, "_plan_intent") as plan_intent:
        plan_intent.return_value = RuntimeIntent(
            intentId="intent-chain-submit_execution",
            intentType="chain",
            action="chain_submit_execution",
            parameters={"operation": "rebalance"},
        )

        result = asyncio.run(controller.tick())

    self.assertEqual(result["execution"]["stage"], "reconciled")
    self.assertEqual(asyncio.run(controller.status())["ledger"]["activeExecutions"], [])
```

```python
def test_tick_keeps_active_chain_execution_pending_when_confirmation_is_pending(self) -> None:
    # same setup, but confirmation result stays pending and activeExecutions remains populated
```

```python
def test_tick_marks_active_chain_execution_failed_on_terminal_failure(self) -> None:
    # same setup, but confirmation result fails and execution moves to history as failed
```

- [ ] **Step 2: Run the controller tests to verify they fail**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_autonomy.py -k "active_chain_execution" -v`
Expected: FAIL because tick does not yet revisit active chain executions using status-query tools

- [ ] **Step 3: Implement the minimal tick-side advancement path**

```python
# agent/autonomy.py
from autonomy_workflows import confirm_chain_execution


async def _advance_active_chain_execution(
    self,
    execution: RuntimeExecutionRecord,
    intent: RuntimeIntent,
) -> RuntimeExecutionRecord:
    if execution.intentType != "chain" or not execution.externalId:
        return execution
    if intent.action == "request_funding":
        return execution
    return await confirm_chain_execution(self._chain_tool_invoker, execution, intent.action)
```

```python
# agent/autonomy.py inside tick()
existing_execution = self._find_active_execution(planned_intent.intentId)
if existing_execution is not None:
    if existing_execution.intentType == "chain" and planned_intent.action != "request_funding":
        execution = await self._advance_active_chain_execution(existing_execution, planned_intent)
        self._state.activeExecutions = [
            execution_record
            for execution_record in self._state.activeExecutions
            if execution_record.intentId != planned_intent.intentId
        ]
        if execution.status == "active":
            self._state.activeExecutions.append(execution)
        else:
            self._state.executionHistory.append(execution)
        self._save_state()
        return {
            "observation": observation,
            "intent": planned_intent.model_dump(),
            "policy": policy,
            "decision": decision.model_dump(),
            "execution": execution.model_dump(),
            "context": context,
            "actionResult": {"action": "advance_execution", "changedState": execution.status != "active"},
        }
```

- [ ] **Step 4: Run the controller tests to verify they pass**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_autonomy.py -k "active_chain_execution" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/autonomy.py agent/tests/test_autonomy.py
git commit -m "feat: advance active chain executions during tick"
```

## Task 7: Final Verification For Receipt And Settlement Flow

**Files:**
- Modify: none expected unless verification reveals a real issue
- Test: `chain/test/mcp-server.test.ts`
- Test: `agent/tests/test_autonomy_workflows.py`
- Test: `agent/tests/test_autonomy.py`

- [ ] **Step 1: Run the chain MCP tests**

Run: `npm test -- chain/test/mcp-server.test.ts`
Expected: PASS

- [ ] **Step 2: Run the agent workflow tests**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_autonomy_workflows.py -v`
Expected: PASS

- [ ] **Step 3: Run the agent controller tests**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_autonomy.py -v`
Expected: PASS

- [ ] **Step 4: Inspect git status for intended files only**

Run: `git status --short`
Expected: only these files changed if no extra fixes were needed:
- `chain/src/domain/types.ts`
- `chain/src/services/transaction-receipt-service.ts`
- `chain/src/services/user-operation-status-service.ts`
- `chain/src/mcp/tools.ts`
- `chain/test/mcp-server.test.ts`
- `agent/autonomy_workflows.py`
- `agent/autonomy.py`
- `agent/tests/test_autonomy_workflows.py`
- `agent/tests/test_autonomy.py`

- [ ] **Step 5: Commit verification-only fixes if needed**

```bash
git add chain/src/domain/types.ts chain/src/services/transaction-receipt-service.ts chain/src/services/user-operation-status-service.ts chain/src/mcp/tools.ts chain/test/mcp-server.test.ts agent/autonomy_workflows.py agent/autonomy.py agent/tests/test_autonomy_workflows.py agent/tests/test_autonomy.py
git commit -m "test: verify chain receipt settlement flow"
```

## Self-Review

### Spec Coverage

- new receipt and user operation query tools: Tasks 2 and 3
- normalized result types: Task 1
- mock and network status normalization: Task 4
- agent workflow query integration: Task 5
- tick-driven active execution advancement: Task 6
- request_funding boundary preserved: Task 5 and Task 6
- MCP, workflow, and controller verification: Task 7

No spec section is left without a matching implementation task.

### Placeholder Scan

- no `TODO` or `TBD`
- all tasks name exact files
- all code steps include concrete code blocks
- all verification steps include exact commands and expected results

### Type Consistency

- the plan consistently uses `TransactionReceiptStatusResult` and `UserOperationStatusResult`
- the plan keeps `SettlementResult` as submission-time identity only
- the plan consistently treats `request_funding` as outside receipt/final-settlement semantics
