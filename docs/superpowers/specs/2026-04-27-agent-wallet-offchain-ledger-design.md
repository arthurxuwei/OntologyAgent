# Agent Wallet Offchain Ledger Service Design

## Context

The current Agent Wallet demo persists owners, agents, x402 services, and x402 payment records in the `agent` service. It can execute direct x402 payment flows, but it does not model the product's primary matched-task payment path: buyer funds are locked, seller work is delivered, and funds are either released or refunded.

The memo `Agent_Wallet_收付款架构讨论备忘.pdf` recommends an MVP offchain ledger for matched A2A tasks. This design implements that first slice as an independent Docker service under `ledger/`, with local JSON persistence for the MVP.

## Scope

Included:

- Independent service in `ledger/`
- Docker Compose service named `ledger`
- JSON-backed ledger state owned by the ledger service
- USDC atomic amount strings only
- Agent ledger accounts keyed by `agentId`
- Append-only ledger entries
- Escrow records with `locked`, `released`, and `refunded` states
- HTTP operations for crediting demo balance, locking escrow, releasing escrow, refunding escrow, and reading ledger state
- Tests for account balances, insufficient funds, status transitions, and persistence

Excluded:

- Postgres schema and migrations
- On-chain escrow contracts
- Circle deposit and withdrawal reconciliation
- Dispute arbitration workflow
- Partial release or partial refund
- Multi-asset balances

## Architecture

`ledger/main.py` owns all accounting rules and HTTP endpoints. The `agent` service must treat it as a remote service boundary and call it over HTTP when it needs ledger behavior.

The service exposes Pydantic models and an `OffchainLedgerStore`:

- `LedgerAccount`: `agentId`, `asset`, `availableAtomic`, `lockedAtomic`, timestamps
- `LedgerEntry`: immutable accounting event with `entryType`, `agentId`, `escrowId`, signed atomic delta fields, and metadata
- `EscrowRecord`: buyer, seller, amount, status, optional task metadata, timestamps
- `LedgerState`: accounts, entries, escrows
- `OffchainLedgerStore`: JSON-backed persistence and mutation boundary

The ledger file path is controlled by `LEDGER_STATE_PATH`, defaulting to `/app/data/offchain_ledger.json` in Docker and `ledger/data/offchain_ledger.json` locally.

## Data Flow

Demo funding calls `credit_agent_balance`, which creates an account if missing and appends a `credit` entry.

Escrow creation checks buyer available balance, subtracts the amount from available, adds it to locked, creates an `EscrowRecord(status="locked")`, and appends an `escrow_lock` entry.

Escrow release requires `locked` status. It subtracts buyer locked balance, adds seller available balance, marks the escrow `released`, and appends `escrow_release` entries.

Escrow refund requires `locked` status. It subtracts buyer locked balance, adds buyer available balance, marks the escrow `refunded`, and appends an `escrow_refund` entry.

All amounts are parsed as non-negative integer strings. Floating point values are rejected.

## API Surface

The ledger FastAPI app exposes:

- `GET /health`
- `GET /ledger/state`
- `POST /ledger/accounts/{agent_id}/credit`
- `POST /ledger/escrows`
- `POST /ledger/escrows/{escrow_id}/release`
- `POST /ledger/escrows/{escrow_id}/refund`

Agent integration can later add a small HTTP client and LangChain tools:

- `agent_wallet_get_ledger_state`
- `agent_wallet_credit_balance`
- `agent_wallet_create_escrow`
- `agent_wallet_release_escrow`
- `agent_wallet_refund_escrow`

Owner checks remain outside this service. The ledger service is an internal service and trusts callers on the Docker network for the MVP. The `agent` service is responsible for mapping authenticated owners to allowed `agentId` values before calling ledger mutations.

## Error Handling

The ledger service returns `400` for invalid amount strings, missing accounts, insufficient available balance, and invalid state transitions. Missing escrow IDs return `404`.

Release and refund are idempotency-safe by status: once an escrow is released or refunded, the opposite action fails and the same action fails with a clear message instead of double-moving funds.

## Testing

Tests cover the domain module directly and the API integration:

- Crediting creates an account and persists an entry
- Escrow creation moves available to locked
- Insufficient balance rejects escrow creation without mutation
- Release moves locked buyer funds to seller available funds
- Refund moves locked buyer funds back to buyer available funds
- Released/refunded escrows cannot be mutated again
- Docker Compose starts the `ledger` service with a writable `ledger/data` volume
