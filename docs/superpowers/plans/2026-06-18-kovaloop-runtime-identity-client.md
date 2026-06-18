# KovaLoop Runtime Identity Client + CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make Phase 1 identity usable end to end: revise the ledger server (remove alias mechanism, make `ownerEmail` optional, add a dynamic `eigenflux` association field, create the wallet only at profile creation, make claim-link lookup-only) and build the Go runtime client + CLI (`kovaloop profile create/update/show`) that generates an Ed25519 keypair, calls the server, persists `.kovaloop/{profile,credentials}.json` to the config volume, signs agent requests, and fixes the installer path bug.

**Architecture:** Two repos. **Part A** (`OntologyAgent/ledger/`, Python/FastAPI) revises the unmerged Phase 1 branch `feat/kovaloop-profile-identity-phase1` (PR #31). **Part B** (`~/cc/kovaloop`, Go) adds the runtime client on a new branch. Part A is the prerequisite contract for Part B.

**Tech Stack:** Python 3, FastAPI, Pydantic v2, SQLite (`OffchainLedgerStore`), `unittest` + `TestClient` (run with `../.venv/bin/python`). Go 1.22 stdlib only (`crypto/ed25519`, `encoding/base64`, `crypto/rand`), subprocess + `httptest` tests.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-18-kovaloop-runtime-identity-client-design.md`.
- Server tests run from `ledger/`: `../.venv/bin/python -m unittest discover -s tests -p "test_*.py"`.
- Go tests run from `~/cc/kovaloop`: `go test ./...`.
- Signing contract (Go must byte-match Python `agent_auth`): message = `agentId + "\n" + timestamp + "\n" + nonce + "\n" + body` (UTF-8); signature + public key use base64url **without padding** (`base64.RawURLEncoding` / Python urlsafe-no-pad); public key = raw 32 bytes; timestamp RFC3339 UTC (`...Z`).
- `.kovaloop/` lives at the config volume root; `credentials.json` is `0600` in a `0700` dir and must be git-ignored.
- Eigenflux info is a non-authoritative association object, never an alias: no uniqueness, resolution, or auth.

---

# Part A — Ledger Server (revise branch `feat/kovaloop-profile-identity-phase1`)

### Task A1: Remove the Phase 1 alias mechanism

**Files:**
- Modify: `ledger/models.py`, `ledger/store.py`, `ledger/services.py`, `ledger/main.py`
- Modify (delete tests): `ledger/tests/test_agent_identity.py`

- [ ] **Step 1: Delete alias models** in `models.py`: remove class `AgentIdentityAlias`, class `AgentAliasInput`, the `aliases` field from `CreateAgentProfileRequest`, the `agentIdentityAliases` field from `LedgerState`. In `ClaimLinkRequest` remove the `alias` field and the `_require_identity` `model_validator`, and revert `agentId` to `agentId: str = Field(min_length=1)`. Remove the now-unused `model_validator` import if nothing else uses it.

- [ ] **Step 2: Delete alias storage** in `store.py`: remove `AgentIdentityAlias` from the `from models import (...)` block, remove `_alias_record_id`, remove the `("agent_identity_aliases", ...)` entry from `LEDGER_COLLECTIONS`, remove methods `list_aliases_for_agent` and `get_profile_id_by_alias`, and change `create_agent_profile` to drop the `aliases` parameter and its alias loops (it now only upserts the profile record).

```python
def create_agent_profile(self, *, profile: AgentProfile) -> AgentProfile:
    def write(connection: sqlite3.Connection) -> AgentProfile:
        existing = self._get_record_by_id(
            connection, "agent_profiles", AgentProfile, profile.agentId
        )
        if existing is not None:
            raise ValueError("agentId already exists")
        return self._upsert_record(
            connection, "agent_profiles", AgentProfile, profile.agentId, profile
        )
    return self._write(write)
```

- [ ] **Step 3: Delete alias service code** in `services.py`: remove `resolve_agent_alias`, remove `AgentIdentityAlias` from imports, and simplify `assemble_profile_payload` to drop alias assembly:

```python
def assemble_profile_payload(profile: AgentProfile) -> dict[str, Any]:
    data = profile.model_dump()
    data.pop("credentialPublicKey", None)
    data.pop("credentialStatus", None)
    return data
```

  In `create_agent_profile_with_wallet`, stop building `aliases` and call `get_store().create_agent_profile(profile=profile)`.

- [ ] **Step 4: Delete alias endpoints/logic** in `main.py`: remove `resolve_agent_alias` from the `from services import (...)` block; delete the `@app.get("/ledger/profiles/resolve")` handler; in `create_agent_profile` remove the `if request.aliases:` dashboard-auth gating block (the body becomes just the try/except calling `create_agent_profile_with_wallet`); in `create_claim_link` revert to `agent_id = request.agentId` and remove the alias-resolution branch (the `or alias` handling) — claim-link rework happens in A4.

- [ ] **Step 5: Delete alias tests** in `tests/test_agent_identity.py`: remove `TestProfileStore.test_alias_resolves_to_canonical_id`, `test_duplicate_alias_rejected`; remove `TestProfileService.test_create_profile_with_alias_persists_alias`; remove `TestProfileEndpoints.test_alias_create_requires_dashboard_auth`, `test_resolve_alias_endpoint`; remove class `TestClaimLinkAlias`. Update any remaining call to `create_agent_profile(... aliases=[])` / `self.store.create_agent_profile(profile=..., aliases=[...])` to the new signature (`profile=...` only).

- [ ] **Step 6: Run full suite — green**

Run: `cd ledger && ../.venv/bin/python -m unittest discover -s tests -p "test_*.py"`
Expected: `OK` (alias tests gone, everything else passes).

- [ ] **Step 7: Commit**

```bash
git add ledger/models.py ledger/store.py ledger/services.py ledger/main.py ledger/tests/test_agent_identity.py
git commit -m "feat(ledger): remove alias mechanism in favor of association field"
```

---

### Task A2: `eigenflux` dynamic association field

**Files:**
- Modify: `ledger/models.py`, `ledger/tests/test_agent_identity.py`

**Interfaces:**
- Produces: `AgentProfile.eigenflux: Optional[dict[str, Any]]`, `CreateAgentProfileRequest.eigenflux: Optional[dict[str, Any]]`; `assemble_profile_payload` returns `eigenflux` verbatim.

- [ ] **Step 1: Write the failing test** — append to `TestProfileService` in `tests/test_agent_identity.py`:

```python
def test_create_profile_carries_eigenflux_object(self) -> None:
    request = CreateAgentProfileRequest(
        agentName="OntologyAgent",
        ownerEmail="owner@example.com",
        credentialPublicKey="pk",
        eigenflux={"id": "312586087945994240", "name": "Old", "bio": "b"},
    )
    with patch.object(services, "get_ledger_wallet_client", return_value=_FakeWalletClient()):
        payload = asyncio.run(services.create_agent_profile_with_wallet(request))
    profile = payload["profile"]
    self.assertEqual(profile["eigenflux"]["id"], "312586087945994240")
    self.assertEqual(profile["eigenflux"]["name"], "Old")
    # round-trips through the store
    stored = services.get_store().get_agent_profile(profile["agentId"])
    self.assertEqual(stored.eigenflux["bio"], "b")
```

- [ ] **Step 2: Run → fail** (`CreateAgentProfileRequest` rejects unknown `eigenflux`).

Run: `cd ledger && ../.venv/bin/python -m unittest discover -s tests -p "test_agent_identity.py"`
Expected: FAIL (pydantic ValidationError / KeyError).

- [ ] **Step 3: Add the field** in `models.py` — add `eigenflux: Optional[dict[str, Any]] = None` to both `AgentProfile` (after `description`) and `CreateAgentProfileRequest` (after `description`). Wire it through in `services.create_agent_profile_with_wallet` when constructing the `AgentProfile`:

```python
profile = AgentProfile(
    agentId=agent_id,
    agentName=request.agentName,
    ownerEmail=owner_email,
    description=request.description,
    eigenflux=request.eigenflux,
    credentialPublicKey=request.credentialPublicKey,
    createdAt=stamp,
    updatedAt=stamp,
)
```

(`assemble_profile_payload` already returns all model fields via `model_dump()`, so `eigenflux` is included. The SQLite store already JSON-encodes dict fields via `serialize_record_field`/`deserialize_record_field` — no store change needed; `LedgerEntry.metadata` uses the same path.)

- [ ] **Step 4: Run → pass.** Same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ledger/models.py ledger/services.py ledger/tests/test_agent_identity.py
git commit -m "feat(ledger): add dynamic eigenflux association field to profiles"
```

---

### Task A3: `ownerEmail` optional + always create wallet at profile creation

**Files:**
- Modify: `ledger/models.py`, `ledger/services.py`, `ledger/tests/test_agent_identity.py`

**Interfaces:**
- Produces: `get_or_create_agent_wallet(request, *, allow_missing_email: bool = False)`.

- [ ] **Step 1: Write the failing test** — append to `TestProfileService`:

```python
def test_create_profile_without_owner_email_still_creates_wallet(self) -> None:
    request = CreateAgentProfileRequest(
        agentName="OntologyAgent",
        credentialPublicKey="pk",
    )  # no ownerEmail
    with patch.object(services, "get_ledger_wallet_client", return_value=_FakeWalletClient()):
        payload = asyncio.run(services.create_agent_profile_with_wallet(request))
    profile = payload["profile"]
    self.assertIsNone(profile["ownerEmail"])
    state = self.ledger_domain_state(profile["agentId"])
    self.assertEqual(state["accounts"][0]["circleWalletId"], "circle-wallet-1")
```

- [ ] **Step 2: Run → fail** (CreateAgentProfileRequest requires ownerEmail; service raises "ownerEmail is required").

- [ ] **Step 3: Implement.**
  - `models.py`: `CreateAgentProfileRequest.ownerEmail: Optional[str] = None` (drop `Field(min_length=1)`); `AgentProfile.ownerEmail: Optional[str] = None`.
  - `services.py` `get_or_create_agent_wallet`: add `*, allow_missing_email: bool = False`; change the guard to:

```python
async def get_or_create_agent_wallet(
    request: AgentWalletRequest, *, allow_missing_email: bool = False
) -> dict[str, Any]:
    owner_email = normalize_email(request.email)
    if owner_email is None and not allow_missing_email:
        raise ValueError("email is required")
    request = request.model_copy(update={"email": owner_email})
    ...
```

  - `services.py` `create_agent_profile_with_wallet`: remove the `if owner_email is None: raise ValueError("ownerEmail is required")` guard (allow None), and call the wallet flow with `allow_missing_email=True`:

```python
owner_email = normalize_email(request.ownerEmail)  # may be None
...
await get_or_create_agent_wallet(
    AgentWalletRequest(
        agentName=saved.agentName,
        agentId=saved.agentId,
        email=owner_email,
        agentDescription=saved.description,
    ),
    allow_missing_email=True,
)
```

- [ ] **Step 4: Run → pass.** Also re-run full suite: `cd ledger && ../.venv/bin/python -m unittest discover -s tests -p "test_*.py"` → `OK`.

- [ ] **Step 5: Commit**

```bash
git add ledger/models.py ledger/services.py ledger/tests/test_agent_identity.py
git commit -m "feat(ledger): make ownerEmail optional, always provision wallet at profile creation"
```

---

### Task A4: claim-link is lookup-only (single wallet creation site)

**Files:**
- Modify: `ledger/main.py`, `ledger/tests/test_dashboard_claims.py`

- [ ] **Step 1: Write the failing test** — append a new test to the dashboard-claims test class (it seeds an account via the store, then expects claim-link to reuse it; and expects 404 when no account):

```python
def test_claim_link_uses_existing_account_and_404s_when_missing(self) -> None:
    # missing profile -> 404
    missing = self.client.post(
        "/ledger/claims/link",
        json={"agentId": "kloop_agent_NONE", "agentName": "A", "email": "o@example.com"},
    )
    self.assertEqual(missing.status_code, 404)
    # seed an account, then claim-link reuses it (no wallet client needed)
    main.get_store().bind_account_wallet(
        agent_id="kloop_agent_SEED",
        agent_name="OntologyAgent",
        email="o@example.com",
        wallet_address="0x1111111111111111111111111111111111111111",
        circle_wallet_id="circle-wallet-1",
        account_type="EOA",
    )
    ok = self.client.post(
        "/ledger/claims/link",
        json={"agentId": "kloop_agent_SEED", "agentName": "OntologyAgent", "email": "o@example.com"},
    )
    self.assertEqual(ok.status_code, 200)
    body = ok.json()
    self.assertEqual(body["circleWalletId"], "circle-wallet-1")
    self.assertTrue(body["claimCode"].startswith("clm_"))
```

- [ ] **Step 2: Run → fail** (claim-link currently calls the wallet client and would not 404).

- [ ] **Step 3: Rewrite `create_claim_link`** in `main.py` to look up the existing account instead of get-or-create:

```python
@app.post("/ledger/claims/link")
async def create_claim_link(request: ClaimLinkRequest) -> dict[str, Any]:
    owner_email = normalize_email(request.email)
    if owner_email is None:
        raise HTTPException(status_code=400, detail="email is required")
    account = get_store().get_account(request.agentId)
    if account is None:
        raise HTTPException(
            status_code=404, detail="profile not found — create a profile first"
        )
    account = account.model_dump()
    claim_code = claim_code_for_account(account, owner_email)
    response = ClaimLinkResponse(
        agentId=str(account.get("agentId") or request.agentId),
        agentName=str(account.get("agentName") or request.agentName),
        ownerEmail=owner_email,
        claimCode=claim_code,
        claimUrl=dashboard_url({"claimCode": claim_code, "agentId": request.agentId}),
        agentUrl=dashboard_url({"agentId": request.agentId}),
        walletAddress=(str(account.get("walletAddress")) if account.get("walletAddress") else None),
        circleWalletId=(str(account.get("circleWalletId")) if account.get("circleWalletId") else None),
        accountType=normalize_wallet_account_type(account.get("accountType")),
    )
    return response.model_dump()
```

  Confirm the store exposes a public account getter (`get_account(agent_id) -> Optional[LedgerAccount]`). If the existing public method has a different name, use it; otherwise add a thin public wrapper around `_get_account`.

- [ ] **Step 4: Migrate existing claim-link tests** in `test_dashboard_claims.py`: the five existing `/ledger/claims/link` tests currently rely on claim-link creating the account. For each, **seed the account first** via `main.get_store().bind_account_wallet(agent_id=..., agent_name=..., email=..., wallet_address=..., circle_wallet_id=..., account_type="EOA")` matching what the test asserts, and drop the `patch.object(services, "get_ledger_wallet_client", ...)` wrapper. For `test_claim_link_endpoint_requires_profile_identity` (empty `agentId`), it now expects 404 (no account) — keep its assertion at 404 instead of 422, or seed nothing and assert 404. For `test_claim_link_endpoint_requires_email_via_route_logic` (blank email), seed an account and keep the 400 email assertion.

- [ ] **Step 5: Run dashboard tests, then full suite**

Run: `cd ledger && ../.venv/bin/python -m unittest discover -s tests -p "test_dashboard_claims.py"` then full suite. Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add ledger/main.py ledger/tests/test_dashboard_claims.py
git commit -m "feat(ledger): claim-link looks up existing account instead of creating a wallet"
```

- [ ] **Step 7: Update PR #31 body** to reflect the revised design (alias removed, association field, ownerEmail optional, single wallet site, claim-link lookup-only), then push.

```bash
git push
```

---

# Part B — Runtime Client + CLI (`~/cc/kovaloop`, new branch)

> Before R1: `cd ~/cc/kovaloop && git checkout -b feat/runtime-identity-client`. All Go file paths below are under `~/cc/kovaloop/`. Add `.kovaloop/` to `.gitignore`.

### Task R1: `.kovaloop/` home resolution + `KOVALOOP_HOME`

**Files:**
- Modify: `internal/kovaloopcli/config.go`
- Create: `internal/kovaloopcli/identity.go`
- Create: `internal/kovaloopcli/identity_test.go`

**Interfaces:**
- Produces: `Config.KovaloopHome string`; `KovaloopDir(cfg Config) string`; `ProfileJSONPath(cfg)`, `CredentialsJSONPath(cfg)`.

- [ ] **Step 1: Write failing test** in `identity_test.go`:

```go
package kovaloopcli

import "testing"

func TestKovaloopDirPrefersConfigRoot(t *testing.T) {
	cfg := Config{WorkspaceDir: "/home/node/.openclaw/workspace"}
	if got := KovaloopDir(cfg); got != "/home/node/.openclaw/.kovaloop" {
		t.Fatalf("got %q", got)
	}
	cfg2 := Config{KovaloopHome: "/custom", WorkspaceDir: "/ws"}
	if got := KovaloopDir(cfg2); got != "/custom/.kovaloop" {
		t.Fatalf("override: got %q", got)
	}
}
```

- [ ] **Step 2: Run → fail.** `cd ~/cc/kovaloop && go test ./internal/kovaloopcli/ -run TestKovaloopDir` → undefined.

- [ ] **Step 3: Implement.** In `config.go`: add `"KOVALOOP_HOME"` to the `ProcessEnv` key list, add `KovaloopHome string` to `Config`, and set `KovaloopHome: env["KOVALOOP_HOME"]` in `ConfigFromEnv`. In `identity.go`:

```go
package kovaloopcli

import "path/filepath"

func kovaloopHomeRoot(cfg Config) string {
	if cfg.KovaloopHome != "" {
		return cfg.KovaloopHome
	}
	if cfg.WorkspaceDir != "" {
		return filepath.Dir(cfg.WorkspaceDir) // config volume root
	}
	if cfg.HermesConfigDir != "" {
		return cfg.HermesConfigDir
	}
	if cfg.WorkingDir != "" {
		return cfg.WorkingDir
	}
	return "."
}

func KovaloopDir(cfg Config) string      { return filepath.Join(kovaloopHomeRoot(cfg), ".kovaloop") }
func ProfileJSONPath(cfg Config) string  { return filepath.Join(KovaloopDir(cfg), "profile.json") }
func CredentialsJSONPath(cfg Config) string {
	return filepath.Join(KovaloopDir(cfg), "credentials.json")
}
```

- [ ] **Step 4: Run → pass.** **Step 5: Commit** `git add -A && git commit -m "feat(runtime): resolve .kovaloop home at config volume root"`.

---

### Task R2: identity — keypair + local file structs

**Files:**
- Modify: `internal/kovaloopcli/identity.go`
- Modify: `internal/kovaloopcli/identity_test.go`

**Interfaces:**
- Produces: `type LocalProfile struct{...}`, `type Credentials struct{...}`, `GenerateCredentials(agentID string) (Credentials, error)` (agentID filled later), `LoadCredentials(path)`, `SaveCredentials(path, Credentials)`, `SaveLocalProfile(path, LocalProfile)`, `b64urlEncode([]byte) string` / `b64urlDecode(string) ([]byte, error)`, `(Credentials).PrivateKey() (ed25519.PrivateKey, error)`.

- [ ] **Step 1: Write failing test:**

```go
func TestGenerateAndPersistCredentials(t *testing.T) {
	dir := t.TempDir()
	pub, priv, err := newKeypair()
	if err != nil { t.Fatal(err) }
	creds := Credentials{SchemaVersion: 1, AgentID: "kloop_agent_X",
		PublicKey: b64urlEncode(pub), PrivateKeySeed: b64urlEncode(priv.Seed()), CreatedAt: "t"}
	path := filepath.Join(dir, "credentials.json")
	if err := SaveCredentials(path, creds); err != nil { t.Fatal(err) }
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0o600 { t.Fatalf("perm %v", info.Mode().Perm()) }
	loaded, err := LoadCredentials(path)
	if err != nil { t.Fatal(err) }
	if loaded.PublicKey != creds.PublicKey { t.Fatal("pubkey mismatch") }
	if _, err := loaded.PrivateKey(); err != nil { t.Fatalf("reconstruct: %v", err) }
}
```

(add imports `os`, `path/filepath`, `testing`.)

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** in `identity.go`:

```go
import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

type LocalProfile struct {
	SchemaVersion int    `json:"schemaVersion"`
	AgentID       string `json:"agentId"`
	AgentName     string `json:"agentName,omitempty"`
}

type Credentials struct {
	SchemaVersion  int    `json:"schemaVersion"`
	AgentID        string `json:"agentId"`
	PublicKey      string `json:"publicKey"`
	PrivateKeySeed string `json:"privateKeySeed"`
	CreatedAt      string `json:"createdAt"`
}

func b64urlEncode(b []byte) string { return base64.RawURLEncoding.EncodeToString(b) }
func b64urlDecode(s string) ([]byte, error) { return base64.RawURLEncoding.DecodeString(s) }

func newKeypair() (ed25519.PublicKey, ed25519.PrivateKey, error) {
	return ed25519.GenerateKey(rand.Reader)
}

func (c Credentials) PrivateKey() (ed25519.PrivateKey, error) {
	seed, err := b64urlDecode(c.PrivateKeySeed)
	if err != nil || len(seed) != ed25519.SeedSize {
		return nil, fmt.Errorf("invalid private key seed")
	}
	return ed25519.NewKeyFromSeed(seed), nil
}

func SaveCredentials(path string, c Credentials) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil { return err }
	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil { return err }
	return os.WriteFile(path, data, 0o600)
}

func LoadCredentials(path string) (Credentials, error) {
	data, err := os.ReadFile(path)
	if err != nil { return Credentials{}, err }
	var c Credentials
	if err := json.Unmarshal(data, &c); err != nil { return Credentials{}, err }
	return c, nil
}

func SaveLocalProfile(path string, p LocalProfile) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil { return err }
	data, err := json.MarshalIndent(p, "", "  ")
	if err != nil { return err }
	return os.WriteFile(path, data, 0o644)
}
```

- [ ] **Step 4: Run → pass. Step 5: Commit** `feat(runtime): ed25519 credentials + local identity files`.

---

### Task R3: signing (cross-language contract)

**Files:**
- Create: `internal/kovaloopcli/signing.go`, `internal/kovaloopcli/signing_test.go`

**Interfaces:**
- Produces: `SigningMessage(agentID, timestamp, nonce, body string) string`; `SignBody(priv ed25519.PrivateKey, agentID, timestamp, nonce, body string) string`; `NewNonce() (string, error)`; `NowTimestamp() string`.

- [ ] **Step 1: Write failing test:**

```go
func TestSignBodyVerifies(t *testing.T) {
	pub, priv, _ := newKeypair()
	msg := SigningMessage("kloop_agent_X", "2026-06-18T00:00:00Z", "n1", "{}")
	if msg != "kloop_agent_X\n2026-06-18T00:00:00Z\nn1\n{}" { t.Fatalf("msg %q", msg) }
	sig := SignBody(priv, "kloop_agent_X", "2026-06-18T00:00:00Z", "n1", "{}")
	raw, err := b64urlDecode(sig)
	if err != nil { t.Fatal(err) }
	if !ed25519.Verify(pub, []byte(msg), raw) { t.Fatal("verify failed") }
}
```

- [ ] **Step 2: Run → fail. Step 3: Implement** `signing.go`:

```go
package kovaloopcli

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/hex"
	"time"
)

func SigningMessage(agentID, timestamp, nonce, body string) string {
	return agentID + "\n" + timestamp + "\n" + nonce + "\n" + body
}

func SignBody(priv ed25519.PrivateKey, agentID, timestamp, nonce, body string) string {
	return b64urlEncode(ed25519.Sign(priv, []byte(SigningMessage(agentID, timestamp, nonce, body))))
}

func NewNonce() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil { return "", err }
	return hex.EncodeToString(b), nil
}

func NowTimestamp() string { return time.Now().UTC().Format(time.RFC3339) }
```

- [ ] **Step 4: Run → pass. Step 5: Commit** `feat(runtime): ed25519 request signing matching server contract`.

---

### Task R4: HTTP PATCH with signed headers

**Files:**
- Modify: `internal/kovaloopcli/http.go`

**Interfaces:**
- Produces: `patchRaw(cfg Config, path string, body []byte, headers map[string]string) ([]byte, error)`.

- [ ] **Step 1: Implement** a PATCH helper mirroring the existing POST path (reuse the shared client + fallback retry), sending the raw `body` bytes and setting `Content-Type: application/json` plus the supplied `headers`. Return the response body; treat non-2xx as an error including the body text. (No standalone unit test — exercised by R6's command test.)

- [ ] **Step 2: Build** `cd ~/cc/kovaloop && go build ./...`. **Step 3: Commit** `feat(runtime): add signed PATCH http helper`.

---

### Task R5: `kovaloop profile create` (idempotent)

**Files:**
- Create: `internal/kovaloopcli/profile_cmd.go`
- Modify: `internal/kovaloopcli/cli.go`
- Create: `tests/kovaloop_profile_test.go`

**Interfaces:**
- Consumes: `KovaloopDir`, `Credentials`, `newKeypair`, `postJSON`, eigenflux `LoadProfile`/`ProfilePath`.
- Produces: CLI path `kovaloop profile create`; server request shape `{agentName, ownerEmail?, description?, eigenflux?, credentialPublicKey}`; reads server response `{profile:{agentId, agentName}}`.

- [ ] **Step 1: Write failing subprocess test** in `tests/kovaloop_profile_test.go` (mirror `kovaloop_transfer_test.go`'s stub+`runKovaloop` pattern): stub `POST /ledger/profiles` returning `{"profile":{"agentId":"kloop_agent_TEST","agentName":"OntologyAgent"}}` and capturing the request body; set `KOVALOOP_HOME=<temp>`, `KOVALOOP_LEDGER_HTTP_URL=<stub>`; run `kovaloop profile create`; assert exit 0, stdout contains `kloop_agent_TEST`, `<temp>/.kovaloop/credentials.json` exists with mode `0600` and a non-empty `publicKey`, `<temp>/.kovaloop/profile.json` has `agentId=kloop_agent_TEST`, and the captured request `credentialPublicKey` equals the stored `publicKey`. Add a second run asserting **no second POST** (continuity) and same agentId. Add a variant that writes an eigenflux profile and asserts the request `eigenflux.id` is forwarded.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** `profile_cmd.go`:

```go
package kovaloopcli

import (
	"encoding/json"
	"fmt"
	"io"
)

type createProfileRequest struct {
	AgentName        string         `json:"agentName"`
	OwnerEmail       string         `json:"ownerEmail,omitempty"`
	Description      string         `json:"description,omitempty"`
	Eigenflux        map[string]any `json:"eigenflux,omitempty"`
	CredentialPubKey string         `json:"credentialPublicKey"`
}

type profileEnvelope struct {
	Profile struct {
		AgentID   string `json:"agentId"`
		AgentName string `json:"agentName"`
	} `json:"profile"`
}

func runProfileCreate(cfg Config, stdout, stderr io.Writer) int {
	credPath := CredentialsJSONPath(cfg)
	if existing, err := LoadCredentials(credPath); err == nil && existing.AgentID != "" {
		fmt.Fprintf(stdout, "Profile already exists: %s\n", existing.AgentID)
		return 0
	}
	pub, priv, err := newKeypair()
	if err != nil { fmt.Fprintln(stderr, err); return 1 }
	_ = priv

	req := createProfileRequest{CredentialPubKey: b64urlEncode(pub), AgentName: "OntologyAgent"}
	if prof, perr := LoadProfile(ProfilePath(cfg)); perr == nil {
		if n := prof.normalizedAgentName(); n != "" { req.AgentName = n }
		req.Description = prof.normalizedDescription()
		req.OwnerEmail = prof.Email
		ef := map[string]any{}
		if id := prof.normalizedAgentID(); id != "" { ef["id"] = id }
		if prof.Email != "" { ef["email"] = prof.Email }
		if n := prof.normalizedAgentName(); n != "" { ef["name"] = n }
		if d := prof.normalizedDescription(); d != "" { ef["bio"] = d }
		if len(ef) > 0 { req.Eigenflux = ef }
	}

	var env profileEnvelope
	if err := postJSON(cfg, "/ledger/profiles", req, &env); err != nil {
		fmt.Fprintln(stderr, err); return 1
	}
	creds := Credentials{SchemaVersion: 1, AgentID: env.Profile.AgentID,
		PublicKey: b64urlEncode(pub), PrivateKeySeed: b64urlEncode(priv.Seed()), CreatedAt: NowTimestamp()}
	if err := SaveLocalProfile(ProfileJSONPath(cfg), LocalProfile{SchemaVersion: 1, AgentID: env.Profile.AgentID, AgentName: env.Profile.AgentName}); err != nil {
		fmt.Fprintln(stderr, err); return 1
	}
	if err := SaveCredentials(credPath, creds); err != nil { fmt.Fprintln(stderr, err); return 1 }
	fmt.Fprintf(stdout, "Profile created: %s\n", env.Profile.AgentID)
	return 0
}

var _ = json.Marshal
```

  In `cli.go`: add a `profile` case to the dispatch that routes `create` → `runProfileCreate(ConfigFromEnv(env), stdout, stderr)` (and `update`/`show` in R6), and add a usage line. Match the existing `runLedger`-style sub-dispatch.

- [ ] **Step 4: Run → pass.** `cd ~/cc/kovaloop && go test ./...`. **Step 5: Commit** `feat(runtime): kovaloop profile create command`.

---

### Task R6: `kovaloop profile update` (signed PATCH) + `profile show`

**Files:**
- Modify: `internal/kovaloopcli/profile_cmd.go`, `internal/kovaloopcli/cli.go`, `tests/kovaloop_profile_test.go`

- [ ] **Step 1: Write failing test:** create credentials (run `profile create` against a stub, or write `credentials.json` directly with a known keypair), then stub `PATCH /ledger/profiles/{id}` capturing headers + body and returning `{"profile":{"agentId":"...","description":"new"}}`; run `kovaloop profile update '{"description":"new"}'`; assert exit 0 and that the captured `X-KovaLoop-Signature` verifies (with `ed25519.Verify`) against the stored `publicKey` over `SigningMessage(agentId, X-KovaLoop-Timestamp, X-KovaLoop-Nonce, body)`, and that all four `X-KovaLoop-*` headers are present.

- [ ] **Step 2: Run → fail. Step 3: Implement** `runProfileUpdate(cfg, args, stdout, stderr)`:

```go
func runProfileUpdate(cfg Config, body string, stdout, stderr io.Writer) int {
	creds, err := LoadCredentials(CredentialsJSONPath(cfg))
	if err != nil { fmt.Fprintln(stderr, "no local credentials"); return 1 }
	priv, err := creds.PrivateKey()
	if err != nil { fmt.Fprintln(stderr, err); return 1 }
	ts := NowTimestamp()
	nonce, err := NewNonce()
	if err != nil { fmt.Fprintln(stderr, err); return 1 }
	sig := SignBody(priv, creds.AgentID, ts, nonce, body)
	headers := map[string]string{
		"X-KovaLoop-Agent-Id":   creds.AgentID,
		"X-KovaLoop-Timestamp":  ts,
		"X-KovaLoop-Nonce":      nonce,
		"X-KovaLoop-Signature":  sig,
	}
	resp, err := patchRaw(cfg, "/ledger/profiles/"+creds.AgentID, []byte(body), headers)
	if err != nil { fmt.Fprintln(stderr, err); return 1 }
	fmt.Fprintln(stdout, string(resp))
	return 0
}
```

  Add `runProfileShow` (GET `/ledger/profiles/{agentId}` via `getRaw`/`getJSON`, print). Wire `update`/`show` into the `profile` dispatch in `cli.go`; `update` takes the JSON body as the next arg.

- [ ] **Step 4: Run → pass. Step 5: Commit** `feat(runtime): signed profile update + profile show`.

---

### Task R7: installer path-layering fix

**Files:**
- Modify: `internal/kovaloopcli/profile.go`, `internal/kovaloopcli/profile_test.go`

- [ ] **Step 1: Write failing test** in `profile_test.go`: set `WorkspaceDir=<tmp>/workspace`, create the eigenflux profile at `<tmp>/.eigenflux/servers/eigenflux/profile.json` (config root, **not** under workspace), assert `ProfilePath(cfg)` returns that config-root path.

- [ ] **Step 2: Run → fail. Step 3: Implement** — in `ProfilePath`, before returning the workspace default, stat a config-root candidate:

```go
if cfg.WorkspaceDir != "" {
	wsCandidate := filepath.Join(cfg.WorkspaceDir, ".eigenflux", "servers", "eigenflux", "profile.json")
	if _, err := os.Stat(wsCandidate); err == nil {
		return wsCandidate
	}
	rootCandidate := filepath.Join(filepath.Dir(cfg.WorkspaceDir), ".eigenflux", "servers", "eigenflux", "profile.json")
	if _, err := os.Stat(rootCandidate); err == nil {
		return rootCandidate
	}
	return wsCandidate
}
```

- [ ] **Step 4: Run → pass** (and `go test ./...` green — confirm existing profile tests still pass). **Step 5: Commit** `fix(runtime): resolve eigenflux profile at config root for claim link`.

---

### Task R8: installer wiring + gitignore

**Files:**
- Modify: `install.sh`, `.gitignore`

- [ ] **Step 1:** In `install_runtime` in `install.sh`, before the `kovaloop claim link` call, run `env "$env_name=$root" "$bin_dest/kovaloop" profile create || true` (idempotent; non-fatal). Add `.kovaloop/` to `.gitignore`.

- [ ] **Step 2: Verify** `bash -n install.sh` parses; `go test ./...` green. **Step 3: Commit** `feat(runtime): create profile during install; ignore .kovaloop`.

- [ ] **Step 4:** Push branch and open PR in the kovaloop repo.

---

## Final Verification

- [ ] Part A: `cd ledger && ../.venv/bin/python -m unittest discover -s tests -p "test_*.py"` → `OK`; routes no longer include `/ledger/profiles/resolve`.
- [ ] Part B: `cd ~/cc/kovaloop && go build ./... && go test ./...` → all pass.
- [ ] Manual contract check: a signature produced by `SignBody` verifies in a Python `agent_auth.verify_agent_signature` call with the same message/keys (b64url no-pad + raw-32B pubkey).

## Spec Coverage Map

| Spec section | Task |
| --- | --- |
| A1 remove alias | A1 |
| A2 ownerEmail optional + eigenflux field | A2, A3 |
| A3 wallet only at creation | A3 |
| A4 claim-link lookup-only | A4 |
| B1 `.kovaloop/` + KOVALOOP_HOME | R1 |
| B2 local files | R2 |
| B3 commands create/update/show | R5, R6 |
| B4 HTTP PATCH | R4 |
| B5 signing contract | R3, R6 |
| B6 installer path fix | R7 |
| B7 installer wiring | R8 |
| Testing | each task + Final Verification |
