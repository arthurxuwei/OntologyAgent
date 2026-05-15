---
name: ontology-chain
description: |
  Backend-only direct chain capability for the local OntologyAgent stack.
  Use for operator-directed chain diagnostics, direct execution, and x402 fetches. Do not use for
  service purchase payment state; agents should learn payment state from ontology-ledger escrow.
metadata:
  author: "OntologyAgent"
  version: "0.1.0"
  requires:
    bins: ["ontology"]
  cliHelps: ["ontology chain --help", "ontology chain wallet-state", "ontology chain tools"]
---

# OntologyAgent — Backend Chain

Use the local `ontology` CLI as the command entrypoint for backend chain operations from ZeroClaw.
Business agents should not reason about chain settlement, gas, receipts, or Circle transfers during
service purchase flows.

## Core Rules

- Chain actions must go through the `chain` MCP service.
- Before any transfer, transaction, UserOperation, x402 fetch, escrow-affecting payment, or paid action, route payment intent first using the ledger skill.
- Service purchase payment state comes only from `ontology ledger state` and escrow records.
- Do not use wallet balances, gas, receipts, transaction status, or Circle wallet state to decide whether a service has been prepaid, paid, released, or refunded.
- Do not bind an operator/user signer address to an agent. If the user says an address is theirs, treat it as forbidden for Agent Wallet ownership.
- Do not create, bind, inspect, or transfer Agent Wallets through the chain MCP service. Use `ontology-circle` for Circle-backed Agent Wallet lifecycle.
- Do not use direct Agent Wallet transfer for service purchase, offer acceptance, prepayment, or final payment between agents. Those flows must use the ledger escrow skill first.

## Quick Reference

### Chain MCP Health

```bash
ontology chain health
```

### List Chain MCP Tools

```bash
ontology chain tools
```

### Wallet State For Operator Diagnostics Only

```bash
ontology chain wallet-state
```

### Raw Chain MCP Tool Call

Use only when the operator asked for the specific diagnostic/settlement operation and all preconditions are satisfied:

```bash
ontology chain call chain_get_transaction_receipt '{"txHash":"0x..."}'
ontology chain call chain_get_user_operation_status '{"userOpHash":"0x..."}'
```

For potentially spending tools such as `chain_sign_transfer`, `chain_submit_execution`,
`chain_submit_user_operation`, and `chain_x402_fetch`, first route
the payment intent and ask for explicit user confirmation.

## Response Guidelines

- For service purchase questions, answer from `ontology ledger state`, not chain state.
- Do not report chain/gas/receipt details unless the operator specifically asked for backend diagnostics.
- Never expose private keys or ask the user to paste them into chat.
