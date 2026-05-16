# ZeroClaw Integration

This directory contains OntologyAgent capabilities packaged in the same style as EigenFlux:

- `skills/ontology-ledger/SKILL.md` teaches ZeroClaw how to use the local ledger and escrow service.
- `skills/ontology-circle/SKILL.md` teaches ZeroClaw how to use the local Circle Agent Wallet MCP service.
- `skills/ontology-a2a-service-trade/SKILL.md` teaches ZeroClaw how to coordinate EigenFlux
  messages with ledger escrow for autonomous buyer/seller service trades.
- `bin/ontology` is the CLI source copied into the runtime for ledger and Circle operations.

Install into the local runtime workspace using the repository-level guide:

```text
../INSTALL.md
```

When starting ZeroClaw, mount the CLI into the container as `/usr/local/bin/ontology` and keep
the existing runtime config/workspace mounts:

```bash
docker run -d --name zeroclaw-eigenflux \
  -p 42617:42617 \
  -v "$PWD/runtime/config:/zeroclaw-data/.zeroclaw" \
  -v "$PWD/runtime/workspace:/zeroclaw-data/workspace" \
  -v "$PWD/runtime/config/bin/ontology:/usr/local/bin/ontology:ro" \
  ghcr.io/zeroclaw-labs/zeroclaw:debian gateway start
```

Useful checks from inside the ZeroClaw container:

```bash
ontology ledger health
ontology ledger state
ontology circle health
ontology circle tools
```

For write or paid actions, ZeroClaw should first run `ontology ledger route '<json-intent>'`
and only continue with the returned allowed action family.
