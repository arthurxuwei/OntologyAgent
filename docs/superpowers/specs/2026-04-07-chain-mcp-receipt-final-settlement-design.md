# Chain MCP Receipt And Final Settlement Design

## Summary

This spec defines the next P0 follow-on needed to make chain execution confirmation trustworthy.

The current system can submit chain actions and persist execution records, but it cannot yet verify final outcome using receipt-level or terminal-status evidence tied to the submitted `txHash` or `userOpHash`. This spec adds two missing layers:

- new `chain MCP` query tools for transaction and user-operation status
- agent runtime integration that promotes chain executions through a two-level state model: submitted or confirmed first, reconciled only after explicit terminal success

The goal is to stop treating submission success as final settlement success, while still keeping the implementation small and aligned with the current repository structure.

## Current Context

The repository already has:

- `chain_submit_execution`
- `chain_submit_user_operation`
- `chain_sign_transfer`
- `chain_get_wallet_state`

The `chain` package already returns a `settlement` object containing a stable identifier for each supported action:

- signed transfer -> `txHash`
- submitted execution -> `txHash`
- submitted user operation -> `userOpHash`

However, the current `chain MCP` surface does not expose a tool that can query the terminal state of those identifiers. Because of that, the `agent` side can only tell that a submission happened, not whether the submitted object actually settled successfully or failed later.

## Scope

### Included

- add query tools to `chain MCP` for transaction and user-operation status
- add result types and services that return normalized terminal-state information
- update the agent chain workflow to consume these tools
- move chain execution lifecycle to a two-level model:
  - submitted or confirmed
  - reconciled only after explicit success terminal state
- add tests for MCP tools, workflow transitions, and tick-driven execution advancement

### Excluded

- event subscriptions or websocket listeners
- background watcher services outside the existing autonomy tick loop
- x402 settlement confirmation changes
- generalized explorer or analytics APIs
- multi-step backoff or distributed retry orchestration

## Design Goals

- keep `chain MCP` responsible for chain facts and final-status lookup
- keep the `agent` responsible for runtime state transitions and recovery
- avoid false `reconciled` states when only submission succeeded
- avoid inventing fake confirmation semantics from unrelated signals such as signer availability
- keep the solution compatible with the current P0 autonomy runtime

## Recommended Approach

Three approaches were considered:

1. query-tools-first architecture
2. expand submit tools to synchronously wait for terminal state
3. let the agent query RPC or bundler services directly

The recommended approach is query-tools-first.

It keeps the layering clean:

- `chain MCP` owns on-chain status lookup and normalization
- `agent` owns autonomous state progression

It also avoids overloading submission tools with long-polling semantics and avoids duplicating chain-specific logic inside the agent runtime.

## MCP Tool Design

Two new `chain MCP` tools should be added.

### 1. `chain_get_transaction_receipt`

Purpose:
- query terminal or pending status for a normal transaction by `txHash`

Input:
- `txHash: string`

Output shape:

```ts
type TransactionReceiptStatusResult = {
  txHash: string;
  found: boolean;
  finalized: boolean;
  success: boolean | null;
  status: "pending" | "success" | "reverted";
  blockNumber: number | null;
  receipt: Record<string, unknown> | null;
  mode: "mock" | "network";
};
```

Semantics:
- `found=false` means no receipt yet
- `found=true, finalized=true, success=true` means terminal success
- `found=true, finalized=true, success=false` means terminal revert or failure

### 2. `chain_get_user_operation_status`

Purpose:
- query terminal or pending status for an ERC-4337 user operation by `userOpHash`

Input:
- `userOpHash: string`

Output shape:

```ts
type UserOperationStatusResult = {
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

Semantics:
- `pending` means no terminal outcome yet
- `success` means terminal success
- `failed` means terminal failure

## MCP Internal Structure

The new tools should not push status-query logic into `mcp/tools.ts` directly.

Recommended additions:

- `chain/src/services/transaction-receipt-service.ts`
- `chain/src/services/user-operation-status-service.ts`

Responsibilities:

- service layer talks to provider or bundler APIs
- normalizes mock and network behavior into stable output types
- tool layer performs schema validation and delegates to the service

This keeps the MCP server file focused on registration and transport concerns.

## Type Additions

The `chain/src/domain/types.ts` file should add normalized result types for the two new tools.

Recommended additions:

- `TransactionReceiptStatusResult`
- `UserOperationStatusResult`

These types should be explicit and tool-facing. Do not overload the existing `SettlementResult` type with status-query semantics; `SettlementResult` should remain the compact submission-time identifier object.

## Mock And Network Semantics

### Mock Mode

Mock mode should return deterministic status results tied to the submitted identifier.

Recommended behavior:

- transaction query returns immediate `success`
- user-operation query returns immediate `success`
- returned payload still uses the same normalized status shape as network mode

This ensures agent tests can exercise end-to-end state progression without needing a live chain.

### Network Mode

Recommended behavior:

- transaction query uses the configured provider to look up receipt by `txHash`
- user-operation query uses bundler or provider status APIs to look up terminal state by `userOpHash`
- if terminal state includes an eventual `txHash`, include it in the tool result

The MCP layer should normalize provider-specific responses into the tool result shape and hide provider-specific details from the agent runtime.

## Agent Runtime State Model

The `agent` side should move chain execution through two different kinds of success-related states.

### Submitted Or Confirmed

Immediately after submission succeeds, the execution has a valid external identifier and can be tracked.

This state means:
- the chain object was created or accepted for processing
- the runtime should remember the identifier
- final settlement is not yet proven

### Reconciled

Only use `reconciled` when the new query tools return explicit terminal success for the submitted identifier.

This means:
- transaction receipt exists and shows success
- or user operation query returns terminal success

The runtime should no longer infer reconciliation from unrelated fields such as `signerConfigured`.

## Agent Workflow Changes

### Submission Phase

When `chain_submit_execution` or `chain_submit_user_operation` returns successfully:

- create a `RuntimeExecutionRecord`
- store the returned external identifier
- keep the stage non-final

Recommended lifecycle after submission:

- `stage = "confirmed"`
- `status = "active"`

This differs from the current weakened approximation, where the runtime has no way to revisit chain execution using a receipt-level query.

### Follow-Up Query Phase

On later ticks, when an active chain execution exists:

- for normal transaction submissions, call `chain_get_transaction_receipt`
- for user operations, call `chain_get_user_operation_status`

State transitions:

1. Pending result
- keep `status = "active"`
- keep stage at `confirmed`
- update query metadata such as `lastCheckedAt` and `checkCount`

2. Terminal success
- move to `stage = "reconciled"`
- move to `status = "completed"`

3. Terminal failure
- move to `stage = "failed"`
- move to `status = "failed"`
- capture failure code and message

## Funding Recommendation Boundary

`request_funding` must remain outside receipt and final-settlement semantics.

It is a local recommendation flow, not a chain-submitted object, so it should not use:

- `chain_get_transaction_receipt`
- `chain_get_user_operation_status`
- receipt-driven reconciliation logic

This keeps local operational advice separate from chain execution tracking.

## Error Handling

The agent runtime should distinguish these cases:

- `receipt_not_found_yet`
- `transaction_reverted`
- `user_operation_failed`
- `confirmation_timeout`
- `confirmation_query_error`

Important distinction:

- a missing terminal result is not the same as a failed chain action
- a status-query tool failure is not the same as on-chain revert

## Timeout Strategy

P0 should use a simple bounded strategy instead of a new scheduler framework.

Recommended execution metadata additions:

- `firstSubmittedAt`
- `lastCheckedAt`
- `checkCount`

If a chain execution stays unresolved beyond the configured check threshold or elapsed time budget:

- mark it `failed`
- set `failureCode = "chain_confirmation_timeout"`

This is sufficient for a continuous tick-driven runtime without introducing a new watcher subsystem.

## Testing Strategy

### MCP Tests

Add MCP coverage for:

- `chain_get_transaction_receipt`
- `chain_get_user_operation_status`
- mock-mode terminal success normalization
- network-mode pending and failure normalization via stubs or fakes

### Agent Workflow Tests

Add workflow coverage for:

- pending chain query result keeps execution active and non-reconciled
- success query result promotes execution to `reconciled/completed`
- failure query result promotes execution to `failed`

### Tick-Level Tests

Add controller coverage for:

- active chain execution is re-queried on later ticks
- terminal success moves the execution from active to history
- terminal failure marks the execution failed
- `request_funding` stays outside this path

## Acceptance Criteria

This sub-project is complete when all of the following are true:

1. `chain MCP` can query transaction receipt status by `txHash`
2. `chain MCP` can query user-operation status by `userOpHash`
3. `agent` no longer treats submission success as reconciliation success
4. `agent` can advance active chain executions on later ticks using the new query tools
5. successful terminal results become `reconciled/completed`
6. failed terminal results become `failed`
7. `request_funding` remains a local recommendation path
8. MCP, workflow, and controller tests cover pending, success, and failure transitions

## Recommended Implementation Order

1. add normalized query result types in `chain/src/domain/types.ts`
2. add receipt and user-operation status services
3. register the new MCP tools in `chain/src/mcp/tools.ts`
4. add MCP tests for tool output shapes and semantics
5. update `agent/autonomy_workflows.py` to use the new tools
6. update `agent/autonomy.py` to revisit active chain executions on later ticks
7. add workflow and controller tests for full state progression

## Final Recommendation

Do not try to solve this by making submission tools wait longer or by teaching the agent to query chain infrastructure directly.

The right boundary is:

- `chain MCP` reports chain facts
- `agent` advances autonomy state based on those facts

That gives the autonomy runtime a trustworthy path from submission to terminal chain outcome without collapsing the architecture.
