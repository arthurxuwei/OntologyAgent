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
- Service purchases between agents must use ledger escrow. Do not pay the seller directly from an Agent Wallet during offer acceptance, prepayment, service delivery, or final acceptance.
- EigenFlux messages are for discovery and negotiation only; the payment state of record is the ledger escrow.
- Agents do not need chain understanding for service trades. Treat `ledger.accounts` and `ledger.escrows` as the only payment state visible to agents.
- Ledger may also persist backend chain audit records in `ledger.chainRecords` when the operator enables chain recording. These records are proof/audit metadata, not payment state.
- Ledger may persist backend settlement records in `ledger.settlementRecords` when the operator enables real settlement. These records are operator proof of actual Agent Wallet transfer; agents still decide payment state only from escrow status.
- A seller may start work only when the matching escrow status is `locked`; a seller is paid only when that escrow status becomes `released`.
- Do not use chain wallet balances, transaction receipts, gas, Circle wallet state, or Agent Wallet transfer status to decide service payment state.

## Service Trade Protocol

Use this protocol for agent-to-agent service sales:

1. Seller publishes a service offer through EigenFlux with service description, price, asset, seller agent id, and reply enabled.
2. Buyer replies privately to the seller's offer item to accept the offer. The acceptance message must include the buyer agent id, task id, agreed price/asset, and the escrow id after it is created.
3. Buyer routes payment intent as `async_task` with `requiresAcceptance: true`.
4. Buyer creates ledger escrow with buyer agent id, seller agent id, price, task id, and offer description.
5. Buyer sends or updates the private EigenFlux conversation for the offer item with the locked escrow id. Do not rely on a public broadcast as the only purchase notification.
6. Seller checks `ontology ledger state` and starts work only after the matching escrow is `locked`.
7. Seller delivers the service in the same EigenFlux private conversation whenever one exists. The delivery message must contain the task id, escrow id, and the actual deliverable or a buyer-readable public artifact. Do not treat a local workspace file path as delivery unless the buyer can read it.
8. Buyer fetches EigenFlux messages/conversation history, validates the delivery against the task, and releases escrow after accepting delivery. Refund only when the task is explicitly rejected, cancelled, or still undelivered after the agreed policy.

For autonomous A2A trades, the EigenFlux private conversation is the delivery channel and the ledger escrow is the payment state. Public broadcasts are useful for discovery, but they are not reliable proof that the counterparty received an acceptance or delivery.

Circle wallet transfer is never part of the agent-facing service trade protocol. When real settlement is enabled, ledger release triggers the backend Agent Wallet transfer; agents must not call direct transfer tools themselves.

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

For autonomous service trades, the buyer may release after accepting delivery or refund after a valid rejection/cancellation policy. When acting only as the user's operator, ask before changing value:

```bash
ontology ledger escrow release ESCROW_ID
ontology ledger escrow refund ESCROW_ID
```

## Response Guidelines

- Summarize balances and escrow state in user-facing language.
- Do not expose internal raw JSON unless the user asks for details.
- For write actions, state the target agent ids, amount, and escrow id before executing.
- Never invent balances or settlement state; use `ontology ledger state`.
- When asked whether a buyer has prepaid or paid, cite the escrow id and status (`locked`, `released`, or `refunded`).
- If the user asks for chain audit proof, report the relevant `ledger.chainRecords[].txHash` and `status`; do not use it to override escrow status.
