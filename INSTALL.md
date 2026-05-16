# Install OntologyAgent CLI

This guide is for agents or operators that need to install the local
`ontology` CLI and OntologyAgent skills into a ZeroClaw-style runtime.

The CLI entrypoint is:

```text
zeroclaw/bin/ontology
```

The installer copies it to:

```text
runtime/config/bin/ontology
```

It also installs the OntologyAgent skills into the local runtime workspace so an
agent can discover the ledger, chain, Circle, and A2A service-trade workflows.

## Prerequisites

- Run commands from the repository root.
- `sh`, `cp`, `rm`, `mkdir`, and `chmod` must be available.
- `curl` must be available wherever the `ontology` CLI runs.
- OntologyAgent services should be running locally or reachable over the network.

For local development, start services with:

```bash
docker compose up -d --build
```

The default local endpoints used by the CLI are:

```text
ledger: http://host.docker.internal:8092
chain:  http://host.docker.internal:8091/mcp/
circle: http://host.docker.internal:8093/mcp/
```

When the CLI runs on the host instead of inside a container, it falls back to:

```text
ledger: http://127.0.0.1:8092
chain:  http://127.0.0.1:8091/mcp/
circle: http://127.0.0.1:8093/mcp/
```

## Install

Run:

```bash
./scripts/install-zeroclaw-ontology.sh
```

The installer copies skills to:

```text
runtime/workspace/skills/ontology-*
runtime/workspace/.agents/skills/ontology-*
```

and copies the CLI to:

```text
runtime/config/bin/ontology
```

To install into a different runtime directory, set `ZEROCLAW_RUNTIME_DIR`:

```bash
ZEROCLAW_RUNTIME_DIR=/path/to/runtime ./scripts/install-zeroclaw-ontology.sh
```

## Verify On The Host

Check that the CLI was installed and is executable:

```bash
runtime/config/bin/ontology help
```

Check ledger connectivity:

```bash
runtime/config/bin/ontology ledger health
runtime/config/bin/ontology ledger state
```

Check MCP connectivity when those services are enabled:

```bash
runtime/config/bin/ontology chain health
runtime/config/bin/ontology chain tools
runtime/config/bin/ontology circle health
runtime/config/bin/ontology circle tools
```

If the service is intentionally not deployed, its health command is expected to
fail. For example, a ledger-and-circle-only deployment should not expose the
standalone chain MCP endpoint.

## Use From A ZeroClaw Container

Mount the installed CLI into the container as `/usr/local/bin/ontology` and keep
the runtime config/workspace mounts:

```bash
docker run -d --name zeroclaw-eigenflux \
  -p 42617:42617 \
  -v "$PWD/runtime/config:/zeroclaw-data/.zeroclaw" \
  -v "$PWD/runtime/workspace:/zeroclaw-data/workspace" \
  -v "$PWD/runtime/config/bin/ontology:/usr/local/bin/ontology:ro" \
  ghcr.io/zeroclaw-labs/zeroclaw:debian gateway start
```

Inside the container, verify:

```bash
ontology ledger health
ontology ledger state
ontology circle health
ontology circle tools
```

If chain MCP is deployed:

```bash
ontology chain health
ontology chain wallet-state
ontology chain tools
```

## Configure Remote Endpoints

Override endpoint URLs with environment variables when services are remote or
when a container cannot reach `host.docker.internal`:

```bash
export ONTOLOGY_LEDGER_URL=http://<lighthouse-ip>:8092
export ONTOLOGY_CIRCLE_MCP_URL=http://<lighthouse-ip>:8093/mcp/
export ONTOLOGY_CHAIN_MCP_URL=http://127.0.0.1:8091/mcp/
```

Fallback URLs can also be set:

```bash
export ONTOLOGY_LEDGER_FALLBACK_URL=http://127.0.0.1:8092
export ONTOLOGY_CIRCLE_MCP_FALLBACK_URL=http://127.0.0.1:8093/mcp/
export ONTOLOGY_CHAIN_MCP_FALLBACK_URL=http://127.0.0.1:8091/mcp/
```

Do not commit private keys, Circle credentials, RPC secrets, or deployment
tokens to this repository. Runtime secrets belong in `.env`, server-local env
files, or GitHub Secrets.

## Agent Safety Rules

Agents should use the installed `ontology` command as their only local entrypoint
for OntologyAgent ledger, chain, and Circle operations.

Before any payment, x402 call, transfer, escrow lock, escrow release, or refund,
the agent must route the intent:

```bash
ontology ledger route '{"deliveryMode":"async_task","requiresAcceptance":true,"amountAtomic":"1000000","asset":"USDC"}'
```

Continue only with the returned `allowedTools` or command family. If the router
returns `needs_clarification`, ask the user for clarification before proceeding.

Use ledger state, not chain or Circle state, to answer whether an A2A service
trade has been prepaid, locked, released, or refunded:

```bash
ontology ledger state
```

## Common Commands

Ledger:

```bash
ontology ledger health
ontology ledger state
ontology ledger credit AGENT_ID AMOUNT_ATOMIC "manual credit"
ontology ledger escrow create '{"buyerAgentId":"agent_buyer","sellerAgentId":"agent_seller","amountAtomic":"1000000","taskId":"task_123","description":"Task settlement"}'
ontology ledger escrow release ESCROW_ID
ontology ledger escrow refund ESCROW_ID
```

Circle:

```bash
ontology circle health
ontology circle tools
ontology circle call agent_wallet_get_or_create '{"agentName":"Example Agent","agentId":"agent_123","email":"agent@example.com"}'
ontology circle call agent_wallet_status '{"walletAddress":"0x..."}'
```

Chain, when deployed:

```bash
ontology chain health
ontology chain tools
ontology chain wallet-state
ontology chain call chain_get_transaction_receipt '{"txHash":"0x..."}'
```

## Update Or Reinstall

After pulling repository changes, rerun:

```bash
./scripts/install-zeroclaw-ontology.sh
```

The installer replaces existing `ontology-*` skills in the runtime workspace and
updates `runtime/config/bin/ontology`.

Restart the agent or ZeroClaw container after reinstalling if it caches skills or
command paths.

## Troubleshooting

If `ontology` is not found inside the container, confirm the bind mount points to
the installed CLI:

```bash
ls -l runtime/config/bin/ontology
```

If the CLI exists but cannot run, make it executable:

```bash
chmod +x runtime/config/bin/ontology
```

If service checks fail from a container, set the `ONTOLOGY_*_URL` environment
variables to reachable service URLs and rerun the health commands.

If an agent cannot use the command, ensure the runtime configuration allows the
`ontology` command and that the installed skills are present under:

```text
runtime/workspace/skills/
runtime/workspace/.agents/skills/
```
