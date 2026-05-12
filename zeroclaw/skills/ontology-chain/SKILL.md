---
name: ontology-chain
description: |
  Chain, wallet, x402, transaction, UserOperation, and settlement capability for the local OntologyAgent
  stack. Use when the user asks about wallet state, Base Sepolia balances, x402 paid fetches,
  signing transfers, submitting transactions, checking receipts, or UserOperation status.
metadata:
  author: "OntologyAgent"
  version: "0.1.0"
  requires:
    bins: ["ontology"]
  cliHelps: ["ontology chain --help", "ontology chain wallet-state", "ontology chain tools"]
---

# OntologyAgent — Chain Wallet & x402

Use the local `ontology` CLI as the command entrypoint for chain operations from ZeroClaw.

## Core Rules

- Chain actions must go through the `chain` MCP service.
- Before any transfer, transaction, UserOperation, x402 fetch, escrow-affecting payment, or paid action, route payment intent first using the ledger skill.
- Summarize the action, destination address or service URL, asset, amount/cap, and expected network before executing.
- Use status/receipt commands after submission; do not infer success from submission alone.
- Do not call hidden Agent Wallet lifecycle tools unless explicitly enabled by the operator.

## Quick Reference

### Chain MCP Health

```bash
ontology chain health
```

### List Chain MCP Tools

```bash
ontology chain tools
```

### Wallet State

```bash
ontology chain wallet-state
```

### Raw Chain MCP Tool Call

Use only when the user asked for the specific operation and all preconditions are satisfied:

```bash
ontology chain call chain_get_transaction_receipt '{"txHash":"0x..."}'
ontology chain call chain_get_user_operation_status '{"userOpHash":"0x..."}'
```

For potentially spending tools such as `chain_sign_transfer`, `chain_submit_execution`,
`chain_submit_user_operation`, `chain_x402_fetch`, and `chain_execute_trade_intent`, first route
the payment intent and ask for explicit user confirmation.

## Response Guidelines

- Prefer `ontology chain wallet-state` for read-only status.
- Report `chainId`, wallet address, ETH balance, USDC balance, and policy caps when relevant.
- For x402, include the target URL, network, asset, and daily/single spend policy before action.
- Never expose private keys or ask the user to paste them into chat.
