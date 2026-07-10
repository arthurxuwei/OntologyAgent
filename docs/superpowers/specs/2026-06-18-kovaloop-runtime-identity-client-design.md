# KovaLoop Runtime Identity Client And CLI Design

## Goal

Phase 1 shipped the **server side** of KovaLoop-native agent identity in the ledger service (profile storage, `kloop_agent_<ULID>` generation, Ed25519 agent-auth primitive, an agent-signature-protected `PATCH`). What is missing is the **client side**: nothing in the agent runtime actually generates a keypair, calls `POST /ledger/profiles`, or persists identity to the mounted volume. The server endpoints have no caller.

This design covers the runtime client and CLI that make Phase 1 usable end to end, plus the server-side adjustments that surfaced once a real caller was designed.

The runtime lives in a **separate repository** — `~/cc/kovaloop` (Go, module `github.com/arthurxuwei/kovaloop`). The ledger service lives in `OntologyAgent/ledger/` (Python/FastAPI). This design therefore spans two repos.

## Current Context

### Runtime (`~/cc/kovaloop`, Go)

- CLI-only, no daemon. Entry point `cmd/kovaloop/main.go` → `internal/kovaloopcli.Run()`, which dispatches on `args[0]` with a hand-rolled switch (no cobra/urfave). Module has **zero external dependencies**, Go 1.22.
- Existing commands: `version`, `ledger health|state|route|wallet get-or-create|transfer`, `claim link`.
- `internal/kovaloopcli/profile.go` reads an **Eigenflux** profile (`{email, agent_id/agentId, agent_name/agentName, bio/agentDescription}`) from `.eigenflux/servers/eigenflux/profile.json`. `ProfilePath()` resolves it relative to `OPENCLAW_WORKSPACE_DIR` / `HERMES_CONFIG_DIR` / PWD.
- `internal/kovaloopcli/http.go` has `getJSON` / `postJSON` with retry + `KOVALOOP_LEDGER_FALLBACK_URL`. Base URL from `KOVALOOP_LEDGER_HTTP_URL` / `KOVALOOP_LEDGER_URL` (default `https://ledger.kovaloop.ai`). **No PATCH, no request signing.**
- **No crypto anywhere** — no keypair, credentials, Ed25519, or signing code.
- Tests are subprocess integration tests: build the binary, run it against an `httptest` `ledgerStub`, assert on stdout/exit/captured requests (`tests/`, `internal/kovaloopcli/*_test.go`).

### Deployment volume layout (`OntologyAgent/docker-compose.openclaw.yml`)

```yaml
volumes:
  - ./runtime-openclaw-x/config:/home/node/.openclaw            # durable per-install volume
  - ./runtime-openclaw-x/workspace:/home/node/.openclaw/workspace # separate mount
environment:
  OPENCLAW_WORKSPACE_DIR: /home/node/.openclaw/workspace
  EIGENFLUX_HOME: /home/node/.openclaw/workspace/.eigenflux
```

The **config mount** (`/home/node/.openclaw`) is the durable, per-install-unique volume the Phase 1 spec requires identity to be anchored to. The **workspace** is a separate mount. The Eigenflux profile lives under the workspace mount in this compose file, but on other deployments it lives at the config root (`~/.openclaw/.eigenflux`) — see the installer bug below.

### Installer path bug (to fix in this work)

`install.sh` sets `OPENCLAW_WORKSPACE_DIR=<workspace>` and then runs `kovaloop claim link`. `ProfilePath()` only looks at `<workspace>/.eigenflux/...`. On deployments where the Eigenflux profile is at the **config root** (`<workspace>/../.eigenflux`, e.g. `~/.openclaw/.eigenflux`), the profile is not found and the post-install claim link fails. This is the same root cause as where `.kovaloop/` should live.

## Decisions (resolved during brainstorming)

1. **`ownerEmail` becomes optional** on the server. Identity is minted at install; ownership is bound later via the OAuth claim. The runtime may not know an owner email at create time.
2. **`.kovaloop/` is anchored to the config volume root** (`/home/node/.openclaw`), not the workspace. A new `KOVALOOP_HOME` env var overrides the resolved location.
3. **The installer path-layering bug is fixed in this work** — `ProfilePath()` also checks the config root (parent of the workspace dir).
4. **Profile creation is an idempotent CLI command** (`kovaloop profile create`) invoked by the installer. The continuity check is built into the command: if `credentials.json` exists, reuse it and no-op. There is no container-startup hook (the image is the external `openclaw:latest`).
5. **This plan includes client-side signing**: keypair generation + storage **and** a `kovaloop profile update` command that sends a signed `PATCH`, proving the Ed25519 agent-auth primitive end to end from the client.
6. **Eigenflux info is carried as a plain association field, not an alias.** Whatever the local Eigenflux profile contains (id, and possibly name/email/bio) is sent to the server and stored verbatim. There is no resolution, uniqueness, first-write-wins, or auth gating on it.
7. **The entire Phase 1 alias mechanism is removed** (it is unused now that association replaces it). PR #31 (unmerged) is revised in place to drop it.
8. **Every new agent gets a Circle wallet at profile creation, as the single creation site.** `claim-link` no longer creates wallets; it looks up the existing account.

## Part A — Ledger Server Changes (`OntologyAgent/ledger/`, revise PR #31)

PR #31 is unmerged; it is amended on the same branch so it lands as a clean server-side Phase 1 rather than merging code that is immediately deleted.

### A1. Remove the alias mechanism

Delete, with their tests:

- `models.py`: `AgentIdentityAlias`, `AgentAliasInput`, the `aliases` field on `CreateAgentProfileRequest`, `agentIdentityAliases` on `LedgerState`, the `alias` field + `model_validator` on `ClaimLinkRequest` (revert `agentId` to required, `Field(min_length=1)`).
- `store.py`: the `agent_identity_aliases` collection, `_alias_record_id`, `create_agent_profile`'s alias handling, `list_aliases_for_agent`, `get_profile_id_by_alias`.
- `services.py`: `resolve_agent_alias`, alias assembly in `assemble_profile_payload`.
- `main.py`: `GET /ledger/profiles/resolve`, the alias-auth gating block in `POST /ledger/profiles`, and the alias-resolution branch in `create_claim_link`.
- Tests: `TestProfileEndpoints.test_alias_*` / `test_resolve_*`, `TestClaimLinkAlias`, store alias tests, service alias tests.

`create_agent_profile` keeps writing the profile; it just no longer takes `aliases`.

### A2. `ownerEmail` optional + dynamic association field

`AgentProfile`:

```python
class AgentProfile(BaseModel):
    schemaVersion: int = 1
    agentId: str
    agentName: str
    ownerEmail: Optional[str] = None        # was: str (required)
    description: Optional[str] = None
    eigenflux: Optional[dict[str, Any]] = None   # NEW dynamic association object
    credentialPublicKey: str
    credentialStatus: Literal["active", "revoked"] = "active"
    createdAt: str
    updatedAt: str
```

`CreateAgentProfileRequest`:

```python
class CreateAgentProfileRequest(BaseModel):
    agentName: str = Field(min_length=1)
    ownerEmail: Optional[str] = None        # was: required min_length=1
    description: Optional[str] = None
    eigenflux: Optional[dict[str, Any]] = None   # NEW
    credentialPublicKey: str = Field(min_length=1)
```

`eigenflux` is a free-form JSON object. The runtime sends at least `{"id": "<eigenflux id>"}` and may include `name`, `email`, `bio`. The server stores and returns it verbatim. No uniqueness, no resolution, no auth. `assemble_profile_payload` includes `eigenflux` in the returned payload (and continues to strip `credentialPublicKey` / `credentialStatus`).

> **Storage note:** the SQLite store serializes each model field to a TEXT column. A `dict` field must be JSON-encoded on write and decoded on read. Confirm `record_from_row` / `record_to_model` round-trip a dict field, or add explicit JSON (de)serialization for `eigenflux` in the store layer.

### A3. Wallet created only at profile creation (always)

`create_agent_profile_with_wallet`:

- No longer raises when `ownerEmail` is absent.
- **Always** provisions the Circle wallet for the new agent (the single creation site), passing `email=owner_email` which may be `None`.

`get_or_create_agent_wallet` (or a profile-creation-specific path) must tolerate a `None` email — the account's `email` is already `Optional`. The current `raise ValueError("email is required")` guard is relaxed for this path. Verify the Circle wallet client does not itself require an email; if it does, pass an empty/synthesized value at the client boundary only, never persisting a placeholder owner.

### A4. `claim-link` looks up the existing account

`create_claim_link` stops calling `get_or_create_agent_wallet`. It fetches the existing account/profile for the `agentId`; if none exists it returns a 404-style error ("profile not found — create a profile first"). Ownership at claim time continues to be recorded by the existing dashboard-claim flow (`dashboardClaimedByEmail`), not by the create-time `ownerEmail`.

### A5. Endpoints after Part A

`POST /ledger/profiles`, `GET /ledger/profiles/{agentId}`, `POST /ledger/profiles/{agentId}/credentials/rotate`, `PATCH /ledger/profiles/{agentId}`. (`/resolve` removed.)

## Part B — Runtime Client + CLI (`~/cc/kovaloop`, Go)

### B1. `.kovaloop/` location + `KOVALOOP_HOME`

New resolution (`config.go` gains `KovaloopHome` from a new `KOVALOOP_HOME` env var; new helper in `identity.go`):

1. `KOVALOOP_HOME` if set.
2. else `filepath.Dir(OPENCLAW_WORKSPACE_DIR)` — the config volume root (`/home/node/.openclaw`).
3. else `HERMES_CONFIG_DIR`.
4. else working dir.

Files: `<home>/.kovaloop/profile.json` and `<home>/.kovaloop/credentials.json`.

### B2. Local files

`profile.json` (minimal; server is authoritative):

```json
{ "schemaVersion": 1, "agentId": "kloop_agent_...", "agentName": "OntologyAgent" }
```

`credentials.json` (dir `0700`, file `0600`):

```json
{ "schemaVersion": 1, "agentId": "kloop_agent_...",
  "publicKey": "<b64url raw 32B>", "privateKeySeed": "<b64url 32B seed>",
  "createdAt": "2026-06-18T..Z" }
```

The private key is stored as the 32-byte Ed25519 seed (`ed25519.PrivateKey.Seed()`); reconstruct with `ed25519.NewKeyFromSeed`. It never leaves the volume.

### B3. Commands (`internal/kovaloopcli/`, dispatched from `cli.go` under `profile`)

- **`kovaloop profile create`** (idempotent; installer calls it):
  1. If `credentials.json` exists → load, print `agentId`, exit 0 (continuity reuse; no POST, no new keypair, no new wallet).
  2. Else generate an Ed25519 keypair.
  3. If an Eigenflux profile exists, read it and build the `eigenflux` object (`{id, name?, email?, bio?}`) plus carry `agentName` / `description` / `ownerEmail` from it.
  4. `POST /ledger/profiles` with `credentialPublicKey` (b64url raw 32B) and the above.
  5. Write `profile.json`, then `credentials.json` (`0600`).
  6. Print the canonical `agentId`.
- **`kovaloop profile update '<json>'`**: load `credentials.json`, build the canonical signed request, send a signed `PATCH /ledger/profiles/{agentId}` updating `description`. Proves client signing.
- **`kovaloop profile show`**: `GET /ledger/profiles/{agentId}` and print.

### B4. HTTP additions (`http.go`)

Add a PATCH helper that sends a raw body with caller-supplied headers (so the exact signed bytes are transmitted). Reuse the existing retry/fallback behavior.

### B5. Signing (`signing.go`) — cross-language contract

Must byte-match the server's `agent_auth`:

- `message = agentId + "\n" + timestamp + "\n" + nonce + "\n" + body` (UTF-8).
- signature = `base64.RawURLEncoding` (no padding) of `ed25519.Sign(priv, message)`.
- registered public key = `base64.RawURLEncoding` of the raw 32-byte public key (matches Python `public_bytes_raw` + urlsafe-no-pad).
- `timestamp` = `time.Now().UTC().Format(time.RFC3339)` (yields trailing `Z`, which the server's `_parse_epoch` accepts).
- `nonce` = random via `crypto/rand`.
- Headers: `X-KovaLoop-Agent-Id`, `X-KovaLoop-Timestamp`, `X-KovaLoop-Nonce`, `X-KovaLoop-Signature`.
- The `PATCH` body signed must be the exact bytes sent (the server reads the raw request body and re-validates), so the client signs its own serialized buffer and transmits that buffer unchanged.

### B6. Installer path-layering fix (`profile.go`)

`ProfilePath()` gains a config-root candidate: when `OPENCLAW_WORKSPACE_DIR` is set, also stat `filepath.Join(filepath.Dir(WorkspaceDir), ".eigenflux", "servers", "eigenflux", "profile.json")` and use it if present. This makes the post-install `kovaloop claim link` find the profile on config-root deployments.

### B7. Installer wiring (`install.sh`)

Before (or in place of) the existing `kovaloop claim link`, run `kovaloop profile create` so a fresh install mints identity. Because `profile create` is idempotent, re-running the installer is safe.

## Testing

### Server (`OntologyAgent/ledger`, `unittest` + `TestClient`)

- Create a profile with **no** `ownerEmail` → succeeds, profile persisted, **wallet created**.
- Create with an `eigenflux` object → stored and returned verbatim (including extra keys).
- `claim-link` for an agent **with** a profile → returns the existing wallet/account; for an agent **without** a profile → error (no wallet created by claim-link).
- Existing signature/rotate/PATCH tests still pass; all `/resolve` and alias tests removed.

### Runtime (`~/cc/kovaloop`, subprocess + `httptest` stub)

- Identity unit: keypair gen, b64url public-key format, signing-message format, sign→`ed25519.Verify` round-trip.
- `profile create`: stub `POST /ledger/profiles` → asserts `profile.json` + `credentials.json` written with `0600`, public key sent matches generated, `agentId` taken from server response.
- Continuity: second `profile create` is a no-op (credentials exist) — **no** second POST.
- Eigenflux carry-over: with an Eigenflux profile present, the request carries `eigenflux.id` (+ name/email/bio) and `agentName`/`description`.
- `profile update`: stub `PATCH` → asserts the four signed headers present and the signature verifies against the sent public key over the canonical message; timestamp + nonce present.
- Path fix: `claim link` finds the profile at the config root (parent of workspace).

## Security

- Private key (seed) is generated locally and never transmitted; only the public key is registered. `credentials.json` is `0600`, in a `0700` dir, on the mounted volume, and must be git-ignored.
- Agent-scoped writes (the `PATCH`) are signed with timestamp + nonce; the server rejects stale/replayed requests (Phase 1 primitive, unchanged).
- The `eigenflux` association is **non-authoritative metadata**: it is not used for resolution, ownership, or auth, so it carries no squatting or impersonation risk. (This is why the Phase 1 alias mechanism — which did have first-write-wins semantics — is removed rather than reused.)
- Removing the create-time alias-auth gating is safe **because there is no alias**; `ownerEmail` is no longer an authorization input at create. Ownership is still established only by dashboard OAuth at claim time.
- All ledger API traffic is HTTPS.

## Non-Goals

- No container-startup hook; continuity runs via the idempotent CLI invoked by the installer.
- No alias / external-id resolution endpoint or claim-by-external-id (the entire alias mechanism is removed).
- No Phase 2 migration of historical Eigenflux-backed ledger rows (separate plan).
- No rollout of agent-signature auth onto value-movement endpoints (transfer/withdrawal/credit) — only the `PATCH` is signed.
- No lost-volume OAuth re-bind UX (rotate endpoint exists server-side; client UX deferred).
- No pooled-custody / lazy wallet cost optimization (MVP keeps eager per-agent wallets).

## Out-of-Scope Follow-ups

- Phase 2 ledger row migration (idempotent, resumable).
- Agent-signature auth on value-movement endpoints.
- Generalizing the `eigenflux` field to a multi-provider association map, if other providers appear.
