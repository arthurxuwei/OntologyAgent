# KovaLoop Profile And Eigenflux Alias Design

## Goal

KovaLoop needs to own agent identity without depending on Eigenflux as the profile source. New agents should receive a KovaLoop canonical `agentId`, and historical Eigenflux ids should be represented as aliases rather than primary identifiers.

The design must answer four questions:

- what the canonical KovaLoop profile looks like
- how KovaLoop generates `agentId`
- how an Eigenflux id maps to a KovaLoop agent
- how KovaLoop verifies that a caller is allowed to act as that agent

## Current Context

The ledger service currently treats `agentId` as the primary account key. It appears in ledger accounts, entries, claim links, dashboard deep links, transfers, withdrawals, onramp sessions, and webhook reconciliation. Existing claim-link design also assumed an Eigenflux profile file at:

```text
workspace/.eigenflux/servers/eigenflux/profile.json
```

That is no longer the desired source of truth. The product name is KovaLoop, and the identity system should be KovaLoop-native.

## Product Naming Decision

KovaLoop is the product name. New user-facing commands, environment variables, documentation, skills, URLs, and profile files should use KovaLoop naming.

Do not preserve historical product naming as a first-class compatibility surface. Any existing historical command or environment variable may be removed or replaced during the migration work. If a temporary shim is required for local rollout, it should be treated as a deployment bridge, not as a documented API.

## Identity Decision

KovaLoop owns the canonical agent identity.

Eigenflux ids become identity aliases:

```json
{
  "provider": "eigenflux",
  "externalId": "312586087945994240"
}
```

Business operations should use the canonical KovaLoop `agentId`. Alias resolution is allowed only at explicit boundary points such as import, migration, and API requests that intentionally accept an external identity.

## Agent Id Generation

KovaLoop generates `agentId` server-side when creating a profile.

Format:

```text
kloop_agent_<ULID>
```

Example:

```text
kloop_agent_01J8Z9M6Y3Q4R8T7N2P5A1B0C9
```

Rules:

- `agentId` is random, stable, and opaque.
- It must not be derived from agent name, email, wallet address, or Eigenflux id.
- It must be URL-safe and CLI-friendly.
- The ledger/profile service is responsible for collision checks.
- Clients may request profile creation, but clients do not choose the canonical id.

ULID is preferred because it is compact, ASCII, time-sortable, and has enough entropy for this use case.

## Canonical Profile

Local profile path:

```text
.kovaloop/profile.json
```

Server-side profile fields:

```json
{
  "schemaVersion": 1,
  "agentId": "kloop_agent_01J8Z9M6Y3Q4R8T7N2P5A1B0C9",
  "agentName": "OntologyAgent",
  "ownerEmail": "owner@example.com",
  "description": "optional agent bio",
  "aliases": [
    {
      "provider": "eigenflux",
      "externalId": "312586087945994240"
    }
  ],
  "createdAt": "2026-06-13T00:00:00Z",
  "updatedAt": "2026-06-13T00:00:00Z"
}
```

Local `profile.json` may mirror the server profile, but the server remains authoritative for alias ownership and claim/wallet operations.

## Agent Credential

Knowing an `agentId` must not be enough to act as that agent.

KovaLoop should create a separate local credential file:

```text
.kovaloop/credentials.json
```

V1 credential model:

- server generates a random `agentSecret` at profile creation
- server stores only a hash of that secret
- local runtime stores the raw secret in `.kovaloop/credentials.json`
- agent calls KovaLoop APIs with the canonical id and bearer credential

Request shape:

```http
X-KovaLoop-Agent-Id: kloop_agent_01J8Z9M6Y3Q4R8T7N2P5A1B0C9
Authorization: Bearer <agentSecret>
```

The server validates the secret hash before accepting any agent-scoped write. This proves the caller is the installed KovaLoop agent instance, not just someone who copied an id from a URL.

V2 can replace bearer secrets with a local keypair and signed requests. The V1 secret model is enough to start, provided credentials are never committed and all remote API traffic uses HTTPS.

## Ledger Data Model

Add server-side profile storage to the ledger service or a ledger-owned identity module.

Recommended records:

### `AgentProfile`

- `agentId`
- `agentName`
- `ownerEmail`
- `description`
- `credentialHash`
- `createdAt`
- `updatedAt`

### `AgentIdentityAlias`

- `provider`
- `externalId`
- `agentId`
- `createdAt`

Constraints:

- `agentId` is unique.
- `(provider, externalId)` is unique.
- An alias can point to only one canonical agent.
- Ledger account rows continue to use canonical `agentId`.

## API Surface

### Create Profile

```http
POST /ledger/profiles
```

Request:

```json
{
  "agentName": "OntologyAgent",
  "ownerEmail": "owner@example.com",
  "description": "optional agent bio",
  "aliases": [
    {
      "provider": "eigenflux",
      "externalId": "312586087945994240"
    }
  ]
}
```

Behavior:

1. normalize owner email
2. reject duplicate aliases that already belong to another profile
3. generate `kloop_agent_<ULID>`
4. generate agent credential secret
5. persist profile, aliases, and credential hash
6. return profile plus the one-time secret

Response:

```json
{
  "profile": {
    "schemaVersion": 1,
    "agentId": "kloop_agent_01J8Z9M6Y3Q4R8T7N2P5A1B0C9",
    "agentName": "OntologyAgent",
    "ownerEmail": "owner@example.com",
    "description": "optional agent bio",
    "aliases": [
      {
        "provider": "eigenflux",
        "externalId": "312586087945994240"
      }
    ],
    "createdAt": "2026-06-13T00:00:00Z",
    "updatedAt": "2026-06-13T00:00:00Z"
  },
  "agentSecret": "<one-time-secret>"
}
```

### Get Profile

```http
GET /ledger/profiles/{agentId}
```

Returns public profile metadata. It never returns the agent secret or credential hash.

### Resolve Alias

```http
GET /ledger/profiles/resolve?provider=eigenflux&externalId=312586087945994240
```

Returns the canonical `agentId` and profile summary. This endpoint is for migration, import, and explicit external-id boundary handling.

### Claim Link

`POST /ledger/claims/link` should accept a canonical `agentId` after a profile exists. Claim-link generation should use the profile owner email and canonical id.

Eigenflux id input is allowed only through explicit alias fields:

```json
{
  "alias": {
    "provider": "eigenflux",
    "externalId": "312586087945994240"
  }
}
```

The endpoint resolves the alias to canonical `agentId`, then continues through the existing Agent Wallet get-or-create flow.

## Local Runtime Flow

### New KovaLoop Agent

1. runtime calls `POST /ledger/profiles`
2. server returns canonical profile and one-time secret
3. runtime writes `.kovaloop/profile.json`
4. runtime writes `.kovaloop/credentials.json`
5. claim link and wallet creation use canonical `agentId`

### Import From Eigenflux

1. migration reads the Eigenflux profile once
2. migration extracts old Eigenflux id, name, email, and optional bio
3. migration calls `POST /ledger/profiles` with an `eigenflux` alias
4. runtime writes `.kovaloop/profile.json`
5. runtime writes `.kovaloop/credentials.json`
6. future runtime reads only `.kovaloop`

After import, Eigenflux is not a runtime dependency.

## Existing Ledger Migration

Historical ledger accounts may already use an Eigenflux id as `agentId`.

Recommended migration:

1. create a KovaLoop profile for each historical account
2. generate a new canonical `kloop_agent_<ULID>`
3. store the old id as `eigenflux` alias
4. update all ledger tables that reference the old `agentId` to the new canonical id
5. keep claim-code generation based on the migrated canonical account record

Tables and records to review during implementation:

- ledger accounts
- ledger entries
- onramp sessions
- circle webhook events
- chain records and settlement record payloads
- dashboard claimed-agent local state
- claim links and dashboard URLs

The target state is that runtime ledger operations write only canonical KovaLoop `agentId`.

## Claim Code Impact

Current claim-code generation includes `agentId`, owner email, and wallet address. Migrating a historical account from an Eigenflux id to a KovaLoop id will change the claim code.

That is acceptable if migration invalidates old claim links and issues new KovaLoop claim links. The migration command should print the new claim URL after profile/account migration.

If preserving old claim links becomes required, that should be a separate explicit compatibility feature. It is not part of this design.

## Security

- `agentSecret` is shown only once at profile creation.
- server stores only credential hashes.
- `.kovaloop/credentials.json` must be ignored by git.
- profile read endpoints never return secrets.
- alias creation must reject duplicates across profiles.
- payment, transfer, withdrawal, funding, and x402 actions still must use existing payment routing rules before value movement.
- agent-scoped write endpoints should require valid agent credentials.
- owner/dashboard actions should continue using dashboard authentication, not agent credentials.

## Testing

Add ledger tests for:

- profile creation generates `kloop_agent_<ULID>`
- duplicate aliases are rejected
- alias resolution returns the canonical profile
- credentials are returned once and stored hashed
- authenticated agent request succeeds with valid secret
- authenticated agent request fails with missing or invalid secret
- claim-link generation uses canonical `agentId`
- migrated Eigenflux alias resolves to the new KovaLoop id

Add migration tests for:

- old account id is replaced across ledger account and entry records
- old Eigenflux id is preserved as alias
- new claim link contains canonical `agentId`
- old claim code is no longer accepted after migration

## Non-Goals

- Do not keep historical product naming as a supported public surface.
- Do not use Eigenflux as a runtime profile source.
- Do not derive KovaLoop `agentId` from Eigenflux id.
- Do not make wallet address the agent identity.
- Do not solve public reputation, service discovery, or marketplace ranking in this spec.
- Do not change payment routing or settlement rules.

## Implementation Decisions

- Profile storage should live in the existing ledger SQLite schema for V1. It is part of the same account, claim, wallet, and dashboard boundary.
- Historical ledger row migration should be a separate operation after profile creation ships. This keeps the first implementation small and makes migration auditable.
- Local profile creation should be exposed through both installer flow and `kovaloop profile create`. The installer uses it automatically, and the CLI gives operators a manual recovery path.

## Recommendation

Build this in two phases.

Phase 1:

- add KovaLoop profile and alias storage
- add server-generated `kloop_agent_<ULID>`
- add V1 agent credentials
- update claim-link generation to use canonical profiles
- write `.kovaloop/profile.json` and `.kovaloop/credentials.json`

Phase 2:

- migrate historical Eigenflux-backed ledger accounts to canonical KovaLoop ids
- store old Eigenflux ids as aliases
- remove Eigenflux profile lookup from runtime flows
- remove historical product naming from active docs and command surfaces

This keeps the new identity model clean while giving the existing ledger state a controlled migration path.
