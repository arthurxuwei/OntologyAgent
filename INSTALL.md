# Install OntologyAgent CLI

Install the `ontology` CLI and OntologyAgent skills into a ZeroClaw-style
runtime. This install path covers `ledger`, `circle`, and A2A service-trade
skills only.

Run from the repository root:

```bash
ROOT_DIR="$(pwd)"
RUNTIME_DIR="${ZEROCLAW_RUNTIME_DIR:-$ROOT_DIR/runtime}"
SKILLS_SRC="$ROOT_DIR/zeroclaw/skills"
BIN_SRC="$ROOT_DIR/zeroclaw/bin/ontology"
WORKSPACE_SKILLS_DEST="$RUNTIME_DIR/workspace/skills"
AGENTS_SKILLS_DEST="$RUNTIME_DIR/workspace/.agents/skills"
BIN_DEST="$RUNTIME_DIR/config/bin"

mkdir -p "$WORKSPACE_SKILLS_DEST" "$AGENTS_SKILLS_DEST" "$BIN_DEST"

for skill_dir in "$SKILLS_SRC"/*; do
  [ -d "$skill_dir" ] || continue
  skill_name="$(basename "$skill_dir")"
  rm -rf "$WORKSPACE_SKILLS_DEST/$skill_name" "$AGENTS_SKILLS_DEST/$skill_name"
  cp -R "$skill_dir" "$WORKSPACE_SKILLS_DEST/$skill_name"
  cp -R "$skill_dir" "$AGENTS_SKILLS_DEST/$skill_name"
done

cp "$BIN_SRC" "$BIN_DEST/ontology"
chmod +x "$BIN_DEST/ontology"
```

This installs:

```text
runtime/config/bin/ontology
runtime/workspace/skills/ontology-*
runtime/workspace/.agents/skills/ontology-*
```

To install into another runtime directory:

```bash
ZEROCLAW_RUNTIME_DIR=/path/to/runtime bash -c '<run the install block above>'
```

## Verify

On the host:

```bash
runtime/config/bin/ontology help
runtime/config/bin/ontology ledger health
runtime/config/bin/ontology ledger state
runtime/config/bin/ontology circle health
runtime/config/bin/ontology circle tools
```

If services are remote, set:

```bash
export ONTOLOGY_LEDGER_URL=http://<host>:8092
export ONTOLOGY_CIRCLE_MCP_URL=http://<host>:8093/mcp/
```

## ZeroClaw Container

Mount the CLI into the container as `/usr/local/bin/ontology`:

```bash
docker run -d --name zeroclaw-eigenflux \
  -p 42617:42617 \
  -v "$PWD/runtime/config:/zeroclaw-data/.zeroclaw" \
  -v "$PWD/runtime/workspace:/zeroclaw-data/workspace" \
  -v "$PWD/runtime/config/bin/ontology:/usr/local/bin/ontology:ro" \
  ghcr.io/zeroclaw-labs/zeroclaw:debian gateway start
```

Inside the container:

```bash
ontology ledger health
ontology ledger state
ontology circle health
ontology circle tools
```

Ensure the runtime config allows the `ontology` command.

## Agent Rules

- Use `ontology` as the local entrypoint for OntologyAgent ledger and Circle operations.
- Before payment, escrow lock, release, or refund, run `ontology ledger route '<json-intent>'`.
- Continue only with the returned `allowedTools` or command family.
- If routing returns `needs_clarification`, ask the user before proceeding.
- Use `ontology ledger state` as the source of truth for A2A service-trade payment state.
