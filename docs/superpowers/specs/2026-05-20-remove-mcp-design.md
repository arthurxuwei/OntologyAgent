# Remove MCP Design

## Decision

Chief will remove MCP from the whole repository. MCP will not remain as a public interface, internal service interface, compatibility layer, hidden runtime path, test target, or documentation concept.

The target architecture is REST plus CLI:

- Agents and ZeroClaw-style runtimes use the `chief` CLI and installed Chief skills.
- `ledger` exposes HTTP REST endpoints and the dashboard.
- `chain` exposes HTTP REST endpoints for chain execution, signing, UserOperations, transaction status, and x402 buyer fetches.
- `circle` exposes HTTP REST endpoints for Agent Wallet lifecycle, Circle Gateway actions, and settlement.
- `agent`, if kept as an interactive orchestrator, uses REST clients and local tools rather than MCP discovery or MCP tool wrappers.

## Why

`chief-install` has already moved the agent-facing surface to a local `chief` CLI plus skills. Its ledger operations call REST endpoints after normalizing the configured URL. Keeping MCP in the core repository now creates two competing control planes:

- model-facing MCP tools inside `agent`
- CLI/REST operations from `chief-install`

That split makes payment safety rules harder to audit. The clean boundary is to make `route_payment_intent` and the returned command/API families the only payment routing contract, regardless of whether an operator uses `chief` or an internal service client.

## Scope

Remove MCP code and references from:

- `agent`: MCP runtime, MCP client, MCP metadata/schema conversion, dynamic skill-declared MCP discovery, MCP health output, and MCP tests.
- `ledger`: FastMCP app, `/mcp/` mount, MCP tool module, MCP service tests, and `LEDGER_*_MCP_URL` configuration.
- `chain`: MCP servers, MCP tool modules, MCP tests, and MCP package dependencies that become unused.
- `circle`: MCP server/tool wrappers and MCP tests. The runtime service remains, but its boundary becomes REST.
- `docker-compose*.yml`: all MCP service aliases, MCP URL environment variables, and comments that imply MCP endpoints.
- `README.md`, `AGENTS.md`, and docs: replace MCP language with REST/CLI language.
- `chief-install`: default hosted ledger URL becomes the service base URL. Its docs stop saying the URL may be an MCP URL.

Out of scope:

- Removing payment routing.
- Removing ledger escrow.
- Removing x402 support.
- Removing Circle Agent Wallet or Gateway settlement.
- Changing spending safety defaults.

## Architecture

### Agent-Facing Interface

The agent-facing interface is `chief`:

- `chief ledger health`
- `chief ledger state`
- `chief ledger route '<json-intent>'`
- `chief ledger wallet get-or-create '<json>'`
- `chief ledger transfer '<json>'`
- `chief ledger escrow create '<json>'`
- `chief ledger escrow release ESCROW_ID`
- `chief ledger escrow refund ESCROW_ID`

The installed Chief skills instruct agents to call `chief ledger route` before any value-changing operation and continue only through the returned command family.

### Ledger REST Interface

`ledger` remains the source of truth for offchain balances, escrow state, onboarding records, onramp sessions, and settlement records. It exposes REST endpoints only.

Existing endpoints stay:

- `GET /health`
- `GET /ledger/state`
- `POST /ledger/wallets/get-or-create`
- `POST /ledger/accounts/{agentId}/credit`
- `POST /ledger/transfers`
- `POST /ledger/escrows`
- `POST /ledger/escrows/{escrowId}/release`
- `POST /ledger/escrows/{escrowId}/refund`
- `POST /onramp/sessions`
- `GET /onramp/sessions/{sessionId}`
- `POST /onramp/sessions/{sessionId}/confirm`

Payment routing is exposed through REST:

- `POST /ledger/payment/route`

The request and response schema match the existing payment router contract. The response includes `method`, `needsClarification`, `allowedTools` or command-family names, and `reason`.

### Chain REST Interface

`chain` becomes an HTTP service. It owns direct chain actions and x402 paid fetches. Endpoint names describe domain actions rather than tools:

- `GET /health`
- `GET /chain/wallet-state`
- `POST /chain/transfers/sign`
- `POST /chain/executions`
- `POST /chain/user-operations`
- `GET /chain/transactions/{hash}`
- `GET /chain/user-operations/{operationId}`
- `POST /x402/fetch`

The request and response payloads reuse the current service-layer command/result types unless those types contain MCP transport fields. Existing caps, whitelists, mock mode, private key handling, and facilitator configuration remain unchanged.

### Circle REST Interface

`circle` becomes an HTTP service for internal wallet lifecycle and settlement operations:

- `GET /health`
- `POST /circle/wallets/get-or-create`
- `GET /circle/wallets/{walletId}`
- `POST /circle/gateway/deposits`
- `POST /circle/gateway/withdrawals`
- `POST /circle/transfers`
- `POST /circle/settlements`

Ledger calls these endpoints for onboarding and settlement when the corresponding features are enabled. Agents do not call `circle` directly.

### Ledger To Chain/Circle Calls

`ledger` stops sending JSON-RPC/MCP requests. It uses small typed HTTP clients:

- `ledger/chain_client.py` for chain audit recording and chain-related calls.
- `ledger/circle_client.py` for wallet onboarding and settlement.

The clients raise clear exceptions that include HTTP status and service name, but they do not leak secrets.

### Agent Service

The `agent` service no longer discovers MCP tools from skill manifests. For this removal, it keeps chat/session orchestration and starts with no value-changing tools. Payment-capable orchestration is reintroduced later through explicit REST-backed local tools after the service boundary is clean.

## Safety Rules

The following rules survive the migration:

- Any funding, payment, x402 call, chain transfer, escrow lock, release, or refund must route payment intent first.
- If routing returns `needs_clarification`, no value-changing call proceeds.
- After routing, callers may use only the returned command/API family.
- x402 is for immediate paid HTTP/API calls.
- Ledger escrow is for asynchronous A2A service settlement.
- Direct Agent transfer is for immediate internal Agent-to-Agent payments without service-trade acceptance or delivery.
- Circle wallet lifecycle and settlement remain backend-controlled.
- Autonomous spending stays disabled by default.
- Live paid tests remain opt-in through explicit credentials and environment variables.

## Migration Strategy

Use test-first migration by subsystem.

1. Add or adjust REST tests for existing ledger behavior, including payment routing and escrow flows.
2. Add REST tests around chain behavior currently covered by MCP tests.
3. Add REST tests around circle behavior currently covered by MCP tests.
4. Replace ledger's chain/circle MCP callers with REST clients.
5. Remove ledger MCP mount and `mcp_tools.py`.
6. Remove chain/circle MCP server/tool wrappers.
7. Remove agent MCP runtime and dynamic skill discovery.
8. Update compose, docs, and install kit defaults.
9. Run ledger unit tests, chain tests, and service health checks.

Each step leaves the repository in a runnable state.

## Testing

Required verification after implementation:

- `cd ledger && python -m unittest discover -s tests`
- `cd chain && npm test`
- `docker compose build ledger`
- `docker compose up -d --build`
- `curl http://localhost:8092/health`
- `curl http://localhost:8091/health`
- `curl http://localhost:8093/health`
- `curl http://localhost:8092/ledger/state`
- `runtime/workspace/.local/bin/chief ledger health` when the runtime install is present

The old MCP tests are deleted or rewritten as REST tests. No test asserts `/mcp`, `McpServer`, `FastMCP`, MCP tool names, or MCP transport behavior.

## Documentation

Docs use these terms:

- Chief CLI
- ledger REST API
- chain REST API
- circle REST API
- payment routing
- allowed command/API family

Docs do not use these terms except in migration history:

- MCP
- MCP tool
- skill provider
- streamable HTTP transport
- `/mcp/`
- `*_MCP_URL`

The main README describes `chief-install` as the agent distribution interface and the core repository as the hosted REST service stack.

## Compatibility

There is no MCP compatibility mode. Existing MCP callers must migrate to the REST/CLI surface. During implementation, if an endpoint already has a REST equivalent, update callers immediately rather than adding an adapter that preserves MCP semantics.

## Risks

- Chain/circle tests may currently exercise behavior only through MCP transport. Rewriting them as REST tests must preserve coverage of service-layer validation and mock/live boundaries.
- Removing agent dynamic MCP discovery may reduce interactive agent capabilities until explicit REST-backed local tools are added.
- Documentation churn is broad. A final repository-wide search for MCP terms is required before completion.

## Acceptance Criteria

- Repository search for MCP implementation terms returns no active code paths.
- No service exposes `/mcp` or documents it as an endpoint.
- `docker-compose.yml` and related compose files contain no MCP URL variables or aliases.
- `chief-install` defaults to a REST service base URL.
- Ledger, chain, and circle communicate through REST.
- Payment routing and escrow safety behavior remain covered by tests.
- Main docs describe the REST/CLI architecture consistently.
