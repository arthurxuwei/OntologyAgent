---
name: ontology-ledger
description: |
  Ledger and escrow capability for the local OntologyAgent stack. Use when the user asks about
  offchain balances, escrow state, A2A settlement, funding/onramp ledger state, payment routing,
  creating escrow, releasing escrow, refunding escrow, or inspecting ledger health.
metadata:
  author: "OntologyAgent"
  version: "0.1.0"
  requires:
    bins: ["ontology"]
  cliHelps: ["ontology ledger --help", "ontology ledger state", "ontology ledger health"]
---

# OntologyAgent — Ledger & Escrow

Use the local `ontology` CLI as the command entrypoint for ledger operations from ZeroClaw.

## Core Rules

- Offchain balances and escrow state live in the standalone `ledger` service.
- Any funding, payment, paid API call, chain transfer, escrow lock, release, or refund must route payment intent first.
- After routing, use only the returned `allowedTools` / command family.
- If routing returns `needs_clarification`, ask the user before funding, paying, locking, releasing, or refunding.
- Escrow is for asynchronous A2A task settlement: create locks buyer balance, release pays seller, refund returns buyer funds.

## Quick Reference

### Health

```bash
ontology ledger health
```

### Ledger State

```bash
ontology ledger state
```

### Route Payment Intent

```bash
ontology ledger route '{"deliveryMode":"async_task","requiresAcceptance":true,"amountAtomic":"1000000","asset":"USDC"}'
```

### Create Escrow

Only after routing allows escrow:

```bash
ontology ledger escrow create '{"buyerAgentId":"agent_buyer","sellerAgentId":"agent_seller","amountAtomic":"1000000","taskId":"task_123","description":"Task settlement"}'
```

### Release Or Refund Escrow

Only after the user confirms the settlement decision:

```bash
ontology ledger escrow release ESCROW_ID
ontology ledger escrow refund ESCROW_ID
```

## Response Guidelines

- Summarize balances and escrow state in user-facing language.
- Do not expose internal raw JSON unless the user asks for details.
- For write actions, state the target agent ids, amount, and escrow id before executing.
- Never invent balances or settlement state; use `ontology ledger state`.
