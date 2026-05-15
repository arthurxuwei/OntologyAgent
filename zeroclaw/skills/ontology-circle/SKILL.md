---
name: ontology-circle
description: |
  Backend-only Circle Agent Wallet lifecycle and settlement capability for the local OntologyAgent stack.
  Use for creating/reusing Agent Wallets, persisting agentId/email/address bindings, and operator
  settlement diagnostics. Service trade payment state still comes from ontology-ledger escrow.
metadata:
  author: "OntologyAgent"
  version: "0.1.0"
  requires:
    bins: ["ontology"]
  cliHelps: ["ontology circle --help", "ontology circle tools"]
---

# OntologyAgent — Circle Agent Wallets

Use the local `ontology` CLI as the command entrypoint for Circle-backed Agent Wallet lifecycle from ZeroClaw.
The Circle MCP service is separate from the chain MCP service.

## Core Rules

- Circle wallet actions must go through the `circle` MCP service.
- For Agent Wallet preparation, first reuse an existing real Circle wallet when one is known.
- Call `agent_wallet_get_or_create` with `agentName`; include `agentId`, `email`, and reusable `circleWalletId` or `walletAddress` when available so the system persists the agent identity binding.
- Do not bind an operator/user signer address to an agent. If the user says an address is theirs, treat it as forbidden for Agent Wallet ownership.
- Do not call lower-level lifecycle tools such as `agent_wallet_init` unless the operator explicitly asks to create a new wallet and no reusable wallet is available.
- Do not use Circle wallet state to decide whether a service has been prepaid, paid, released, or refunded. Use `ontology ledger state` and escrow records for business payment state.
- Do not directly settle a service purchase from an agent conversation. Service purchase settlement must release ledger escrow; the ledger backend calls Circle settlement if real settlement is enabled.

## Quick Reference

### Circle MCP Health

```bash
ontology circle health
```

### List Circle MCP Tools

```bash
ontology circle tools
```

### Agent Wallet Preparation

```bash
ontology circle call agent_wallet_get_or_create '{"agentName":"ZeroClaw EigenFlux Peer","agentId":"312877741349273600","email":"xw007120@163.com","walletAddress":"0x..."}'
```

Successful results include a `binding` object with `agentName`, `agentId`, `email`, and `walletAddress`.

### Circle Settlement Diagnostics

```bash
ontology circle call agent_wallet_status '{"walletAddress":"0x..."}'
ontology circle call agent_wallet_transaction_status '{"transactionId":"..."}'
```

Use settlement status only as operator proof. Do not use it as the agent-facing service trade state.

## Response Guidelines

- For wallet setup, report whether the wallet was reused or created and include the persisted identity binding.
- For service purchase questions, answer from `ontology ledger state`, not Circle transfer state.
- Never expose private keys, entity secrets, API keys, or ciphertext secrets.
