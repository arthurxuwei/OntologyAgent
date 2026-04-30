# Pure Agent Core With Dynamic Skill-Loaded MCP Capabilities

## Goal

Make `agent` a pure orchestration core with no financial-domain implementation code. Domain knowledge and capabilities such as x402, Agent Wallet, ledger, escrow, payment routing, chain operations, and trading should be loaded dynamically through enabled skills and external MCP servers.

The Agent Core should not know what a ledger, escrow, x402 payment, or Agent Wallet is. It should only know how to load skills, connect to MCP servers declared by those skills, discover tools, expose allowlisted tools to the LLM, and execute those tools.

## Non-Goals

- Do not preserve the Agent Wallet MVP web panel inside `agent`.
- Do not keep `/agent-wallet/*` APIs in `agent`.
- Do not keep payment routing, ledger, wallet, or x402 implementation modules inside `agent`.
- Do not replace the existing `chain` or `freqtrade` MCP services unless their contracts need small compatibility changes.
- Do not introduce a new domain-specific router inside Agent Core.

## Target Architecture

`agent` becomes Pure Agent Core:

- Load model and runtime configuration.
- Serve generic chat/session APIs.
- Load enabled skill manifests.
- Build the prompt from a minimal base prompt plus enabled skill instructions.
- Connect to MCP servers declared by enabled skills.
- Discover MCP tool metadata and input schemas.
- Expose only skill-allowlisted tools to the LLM.
- Call MCP tools and return tool results.
- Report skill and MCP health.

Domain capability services own domain logic:

- `chain` MCP owns chain wallet state, signing, transaction execution, UserOperations, x402 buyer flow, and trade intent execution.
- `freqtrade` MCP owns trading status, signal evaluation, bot control, and trade intent emission.
- A new `wallet-ledger-payment` MCP owns Agent Wallet state, ledger balances, escrow flows, payment routing, A2A settlement, and any wallet owner identity/claim flow that remains needed.

## Proposed Repository Shape

```text
agent/
  main.py
  skill_loader.py
  mcp_runtime.py
  prompt_builder.py
  tool_schema.py
  web/chat.html
  skills/
    chain-wallet/
    freqtrade/
    ledger-escrow/
    payment-routing/
    agent-wallet/

chain/
freqtrade/

wallet-ledger-payment/
  server.py
  wallet_state.py
  payment_router.py
  ledger_store.py
  tools/
    wallet_tools.py
    ledger_tools.py
    payment_tools.py
```

The exact Python filenames in `wallet-ledger-payment` can change during implementation, but the boundary should not: Agent Core must not import from this service.

## Skill Manifest v2

Skills are the single dynamic loading unit. A skill declares instructions, MCP servers, and tool allowlists.

```json
{
  "name": "payment-routing",
  "enabled": true,
  "description": "Route payment intents before settlement actions.",
  "instructions": "instructions.md",
  "mcpServers": {
    "wallet-ledger-payment": {
      "urlEnv": "WALLET_LEDGER_PAYMENT_MCP_URL",
      "tools": ["route_payment_intent"]
    }
  },
  "hiddenTools": {
    "wallet-ledger-payment": []
  }
}
```

Agent startup flow:

1. Read `agent/skills/*/skill.json`.
2. Ignore disabled skills.
3. Load enabled skill instructions.
4. Resolve each declared MCP server URL from `urlEnv`.
5. Connect to those MCP servers.
6. Discover server tools and schemas.
7. Expose only tools present in the skill allowlist.
8. Report unavailable servers or invalid tool schemas as degraded health, not fatal startup errors.

If no enabled skill declares a domain tool, the Agent does not see that domain tool or its instructions.

## Agent Core Responsibilities

Agent Core may contain:

- Generic chat/session request and response models.
- Generic LLM setup.
- Generic MCP discovery and invocation code.
- Generic MCP JSON schema to `StructuredTool` conversion.
- Skill manifest parsing.
- Prompt assembly.
- Health reporting for skills and MCP servers.
- Tests that enforce dynamic tool exposure and clean boundaries.

Agent Core must not contain:

- x402 payment code.
- Agent Wallet claim, owner, service, or payment state code.
- Ledger account or escrow mutation code.
- Payment routing decisions.
- Chain transaction wrappers that encode domain behavior.
- Freqtrade business rules beyond generic MCP invocation.
- Hardcoded financial-domain tool registry entries.
- Financial-domain prompt rules outside skill instruction files.

## Domain Capability Ownership

### `chain` MCP

Keep the existing chain MCP as the owner of:

- Wallet state.
- Signing.
- Transaction submission.
- UserOperation submission and status.
- x402 buyer fetch.
- Chain trade intent execution.

Agent Core should discover these tools dynamically when a skill such as `chain-wallet` declares them.

### `freqtrade` MCP

Keep the existing Freqtrade MCP as the owner of:

- Trading status.
- Strategy listing.
- Signal evaluation.
- Open and closed trades.
- Dry-run budget snapshot.
- Bot start, stop, pause, and resume.
- Trade intent emission.

Agent Core should not hardcode the trade intent bridge. If a bridge remains needed, it should live in an MCP server as a tool.

### `wallet-ledger-payment` MCP

Create a new MCP service for:

- Payment routing.
- Ledger state.
- Ledger credit.
- Escrow creation.
- Escrow release.
- Escrow refund.
- Agent Wallet state.
- Agent Wallet claim or ownership, if still needed.
- A2A service registration and x402 service-call bookkeeping, if still needed.

This service can internally reuse code from current `agent` modules, but Agent Core must only call it through MCP.

## Web and API Changes

Remove Agent Wallet MVP UI and APIs from `agent`:

- Remove Agent Wallet panel from `agent/web/chat.html`.
- Remove `/agent-wallet/*` routes.
- Remove wallet-specific GitHub owner session routes if their only purpose is Agent Wallet ownership.

Keep generic chat/runtime APIs:

- `/`
- `/chat`
- `/health`
- `/agent/sessions`
- `/agent/sessions/{session_id}`
- `/agent/sessions/{session_id}/messages/stream`
- `/agent/run`
- `/agent/reload-runtime`

If wallet UI is needed later, it should be served by `wallet-ledger-payment` or a separate frontend.

## Error Handling

- A missing MCP server degrades only skills that depend on it.
- Invalid MCP tool schemas cause the affected tool to be skipped and reported in health.
- Tool call failures are returned as tool errors without Agent Core adding domain-specific interpretation.
- Disabled skills remove both tool exposure and instructions.
- Duplicate tool names across enabled skills should fail closed by default unless explicitly namespaced or deduplicated by configuration.

## Security and Safety

Agent Core enforces generic safety:

- Do not expose undeclared MCP tools.
- Do not call tools outside skill allowlists.
- Do not synthesize tool results.
- Do not silently continue after MCP schema validation failure.

Domain safety moves into skills and MCP contracts:

- Payment routing requirements live in `payment-routing/instructions.md` and the `route_payment_intent` MCP contract.
- Ledger and escrow rules live in `wallet-ledger-payment`.
- Chain limits and x402 spend policy live in `chain`.
- Trading write safety lives in `freqtrade`.

## Migration Plan

### Phase 1: Prepare Pure Core Interfaces

- Add `mcp_runtime.py` for MCP server connection, discovery, and invocation.
- Add `prompt_builder.py` for minimal prompt plus skill instructions.
- Add `tool_schema.py` for MCP schema to LangChain tool conversion.
- Upgrade skill manifests to v2 while preserving compatibility with current manifests during migration.
- Keep existing behavior working until domain MCP services are ready.

### Phase 2: Move Domain Tools to MCP

- Create `wallet-ledger-payment` MCP.
- Move payment routing into this MCP.
- Move ledger tool wrappers into this MCP.
- Move Agent Wallet state and service flows into this MCP or remove them if no longer needed.
- Replace Agent local tool registries with skill-declared MCP tools.

### Phase 3: Remove Agent Wallet UI and APIs

- Delete Agent Wallet panel from `chat.html`.
- Delete `/agent-wallet/*` routes from Agent Core.
- Delete wallet-specific auth routes if they are not needed for generic chat.
- Remove Agent Wallet state persistence from Agent Core.

### Phase 4: Enforce Clean Core

- Add static tests that fail if Agent Core imports domain modules.
- Add tests that disabled skills remove both tools and instructions.
- Add tests that skill allowlists are the only source of exposed tools.
- Add health tests for degraded MCP servers.

## Testing Strategy

Agent Core tests:

- Skill loader reads v2 manifests.
- Prompt builder includes enabled skill instructions only.
- MCP runtime discovers tools from mock MCP servers.
- Tool exposure matches skill allowlists.
- Disabled skills hide tools and instructions.
- Missing MCP server marks skill degraded.
- Agent Core imports no banned domain modules.

Domain MCP tests:

- `wallet-ledger-payment` payment routing decisions.
- Ledger credit and escrow state transitions.
- Agent Wallet state and ownership flows if retained.
- Tool schemas remain valid MCP schemas.

Integration tests:

- With `chain-wallet` enabled, chain tools appear.
- With `payment-routing` disabled, `route_payment_intent` disappears.
- With `ledger-escrow` enabled but MCP down, `/health` reports degraded and tools are absent.
- Generic chat still works without any domain skills enabled.

## Acceptance Criteria

- `agent/main.py` does not import x402, wallet, ledger, payment routing, or chain-domain implementation modules.
- `agent` has no `/agent-wallet/*` endpoints.
- `agent/web/chat.html` has no Agent Wallet panel.
- All domain tools exposed to the LLM come from MCP discovery and skill allowlists.
- Financial-domain instructions live in skill instruction files, not Agent Core constants.
- Disabling a skill removes its tools and instructions without code changes.
- Agent can start with zero domain skills enabled.
- `wallet-ledger-payment` MCP owns payment routing, ledger, escrow, and Agent Wallet domain state if those features remain available.
