# OpenClaw Claim Link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OpenClaw Chief installation print claim and agent dashboard links, and make the dashboard claim automatically from `claimCode + agentId` deep links.

**Architecture:** `chief-install` becomes OpenClaw-only: it discovers OpenClaw workspaces, installs Chief into each workspace, reads the workspace EigenFlux profile, and calls a new ledger claim-link REST endpoint. `OntologyAgent` ledger owns claim metadata generation and dashboard URL construction; the browser dashboard owns login return preservation and local claim activation after authentication.

**Tech Stack:** POSIX shell, Python `unittest`, FastAPI/Pydantic, ledger service helpers, browser-side React-in-HTML dashboard code, `fastapi.testclient`.

---

## File Map

`/Users/freedom/cc/chief-install/install.sh`
: OpenClaw-only installer. Discover `runtime-openclaw-*/workspace`, install CLI and skills, then try to print claim links for each workspace.

`/Users/freedom/cc/chief-install/bin/chief`
: Chief CLI. Add `chief claim link`, OpenClaw profile lookup, profile-to-claim request body construction, and readable claim-link output.

`/Users/freedom/cc/chief-install/tests/test_chief_transfer.py`
: Existing CLI tests. Extend the test HTTP server to handle `/ledger/claims/link`, add claim command tests, and update profile lookup assumptions to OpenClaw-only paths.

`/Users/freedom/cc/chief-install/tests/test_install_openclaw.py`
: New installer tests for OpenClaw workspace discovery, multi-workspace install, install-time claim link attempt, and non-fatal link generation failure.

`/Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger/main.py`
: Add claim request/response models, hosted dashboard URL helper, `POST /ledger/claims/link`, GitHub login return URL support, and callback redirect preservation.

`/Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger/tests/test_ledger_service.py`
: Add endpoint, URL, validation, and login-return tests. Keep existing dashboard tests green.

`/Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger/web/dashboard.html`
: Parse `claimCode` and `agentId`, initiate login with return URL when unauthenticated, auto-claim exact candidate after authentication, and strip claim params after success.

`/Users/freedom/cc/chief-install/INSTALL.md`
: Update install instructions to OpenClaw-only and document claim link output/retry.

## Task 1: Add Ledger Claim Link Endpoint

**Files:**
- Modify: `/Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger/main.py`
- Test: `/Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger/tests/test_ledger_service.py`

- [ ] **Step 1: Write failing endpoint tests**

Add these tests near the existing dashboard/claimable-agent tests in `ledger/tests/test_ledger_service.py`:

```python
    def test_claim_link_endpoint_creates_wallet_and_returns_urls(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {
                    "circleWalletId": "circle-wallet-1",
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "mode": "circle",
                    "binding": {
                        "agentName": request.agentName,
                        "agentId": request.agentId,
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "circleWalletId": "circle-wallet-1",
                        "circleWalletSetId": "circle-wallet-set",
                        "blockchain": "BASE-SEPOLIA",
                        "mode": "circle",
                        "updatedAt": main.now_iso(),
                    },
                }

        with patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://ledger.example.test"}), patch.object(
            main,
            "get_ledger_wallet_client",
            return_value=FakeWalletClient(),
        ):
            response = self.client.post(
                "/ledger/claims/link",
                json={
                    "agentId": "312586087945994240",
                    "agentName": "OpenClaw OntologyAgent",
                    "email": "OWNER@example.com",
                    "agentDescription": "OpenClaw profile bio",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], "312586087945994240")
        self.assertEqual(payload["agentName"], "OpenClaw OntologyAgent")
        self.assertEqual(payload["ownerEmail"], "owner@example.com")
        self.assertTrue(payload["claimCode"].startswith("clm_"))
        self.assertIn("claimCode=" + payload["claimCode"], payload["claimUrl"])
        self.assertIn("agentId=312586087945994240", payload["claimUrl"])
        self.assertEqual(
            payload["agentUrl"],
            "https://ledger.example.test/dashboard?agentId=312586087945994240",
        )
        self.assertEqual(payload["walletAddress"], "0x1111111111111111111111111111111111111111")
        self.assertEqual(payload["circleWalletId"], "circle-wallet-1")

    def test_claim_link_endpoint_requires_profile_identity(self) -> None:
        response = self.client.post(
            "/ledger/claims/link",
            json={
                "agentId": "",
                "agentName": "OpenClaw OntologyAgent",
                "email": "owner@example.com",
            },
        )

        self.assertEqual(response.status_code, 422)
```

- [ ] **Step 2: Run endpoint tests and verify RED**

Run:

```bash
cd /Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger
PYTHONPATH=. /Users/freedom/cc/OntologyAgent/.venv/bin/python -m unittest \
  tests.test_ledger_service.LedgerServiceTests.test_claim_link_endpoint_creates_wallet_and_returns_urls \
  tests.test_ledger_service.LedgerServiceTests.test_claim_link_endpoint_requires_profile_identity
```

Expected: FAIL because `/ledger/claims/link` returns 404.

- [ ] **Step 3: Implement claim models and URL helper**

In `ledger/main.py`, add this constant near the GitHub URL constants:

```python
DEFAULT_PUBLIC_LEDGER_URL = "https://ledger.curawealth.ai"
```

Add these models near `AgentWalletRequest`:

```python
class ClaimLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agentId: str = Field(min_length=1)
    agentName: str = Field(min_length=1)
    email: str = Field(min_length=1)
    agentDescription: Optional[str] = None


class ClaimLinkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agentId: str
    agentName: str
    ownerEmail: str
    claimCode: str
    claimUrl: str
    agentUrl: str
    walletAddress: Optional[str] = None
    circleWalletId: Optional[str] = None
```

Add helpers near `public_base_url()`:

```python
def configured_public_base_url() -> str:
    configured = os.getenv("PUBLIC_BASE_URL")
    if configured and configured.strip():
        return configured.strip().rstrip("/")
    return DEFAULT_PUBLIC_LEDGER_URL


def dashboard_url(path_query: dict[str, str]) -> str:
    return f"{configured_public_base_url()}/dashboard?{urlencode(path_query)}"
```

- [ ] **Step 4: Implement `/ledger/claims/link`**

Add this route after `/ledger/payment/route`:

```python
@app.post("/ledger/claims/link")
async def create_claim_link(request: ClaimLinkRequest) -> dict[str, Any]:
    owner_email = normalize_email(request.email)
    if owner_email is None:
        raise HTTPException(status_code=400, detail="email is required")

    try:
        payload = await get_or_create_agent_wallet(
            AgentWalletRequest(
                agentName=request.agentName,
                agentId=request.agentId,
                email=owner_email,
                agentDescription=request.agentDescription,
            )
        )
    except Exception as error:
        raise http_error(error) from error

    account = payload.get("account")
    wallet = payload.get("wallet")
    if not isinstance(account, dict):
        raise HTTPException(status_code=502, detail="claim link response missing account")
    if not isinstance(wallet, dict):
        wallet = {}

    claim_code = claim_code_for_account(account, owner_email)
    response = ClaimLinkResponse(
        agentId=str(account.get("agentId") or request.agentId),
        agentName=str(account.get("agentName") or request.agentName),
        ownerEmail=owner_email,
        claimCode=claim_code,
        claimUrl=dashboard_url({"claimCode": claim_code, "agentId": request.agentId}),
        agentUrl=dashboard_url({"agentId": request.agentId}),
        walletAddress=(
            str(account.get("walletAddress") or wallet.get("walletAddress"))
            if account.get("walletAddress") or wallet.get("walletAddress")
            else None
        ),
        circleWalletId=(
            str(account.get("circleWalletId") or wallet.get("circleWalletId"))
            if account.get("circleWalletId") or wallet.get("circleWalletId")
            else None
        ),
    )
    return response.model_dump()
```

- [ ] **Step 5: Run endpoint tests and verify GREEN**

Run the same command from Step 2.

Expected: PASS with `Ran 2 tests`.

- [ ] **Step 6: Run full ledger tests**

Run:

```bash
cd /Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger
PYTHONPATH=. /Users/freedom/cc/OntologyAgent/.venv/bin/python -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```bash
cd /Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest
git add ledger/main.py ledger/tests/test_ledger_service.py
git commit -m "feat: add ledger claim link endpoint"
```

## Task 2: Preserve Claim Return URL Through GitHub Login

**Files:**
- Modify: `/Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger/main.py`
- Test: `/Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger/tests/test_ledger_service.py`

- [ ] **Step 1: Write failing login return tests**

Add these tests near the existing GitHub login/callback tests:

```python
    def test_github_login_accepts_dashboard_return_path(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GITHUB_CLIENT_ID": "github-client",
                "GITHUB_CLIENT_SECRET": "github-secret",
                "AUTH_SESSION_SECRET": "session-secret",
                "PUBLIC_BASE_URL": "https://ledger.example.test",
            },
        ):
            response = self.client.get(
                "/auth/github/login?returnTo=/dashboard%3FclaimCode%3Dclm_abc%26agentId%3Dagent_1",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 307)
        set_cookie = response.headers["set-cookie"]
        self.assertIn("chief_ledger_oauth_return=", set_cookie)
        self.assertIn("/dashboard%3FclaimCode%3Dclm_abc%26agentId%3Dagent_1", set_cookie)

    def test_github_callback_redirects_to_stored_claim_return_path(self) -> None:
        async def fake_fetch_github_user(_code, redirect_uri=None):
            return {
                "provider": "github",
                "login": "octo",
                "name": "Octo User",
                "email": "owner@example.com",
                "avatar_url": None,
            }

        with patch.dict(os.environ, {"AUTH_SESSION_SECRET": "session-secret"}), patch.object(
            main,
            "fetch_github_user",
            side_effect=fake_fetch_github_user,
        ):
            response = self.client.get(
                "/auth/github/callback?code=abc&state=oauth-state",
                headers={
                    "Cookie": (
                        "chief_ledger_oauth_state=oauth-state; "
                        "chief_ledger_oauth_return=/dashboard%3FclaimCode%3Dclm_abc%26agentId%3Dagent_1"
                    )
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response.headers["location"],
            "/dashboard?claimCode=clm_abc&agentId=agent_1",
        )
        self.assertIn("chief_ledger_oauth_return=", response.headers["set-cookie"])
```

- [ ] **Step 2: Run login return tests and verify RED**

Run:

```bash
cd /Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger
PYTHONPATH=. /Users/freedom/cc/OntologyAgent/.venv/bin/python -m unittest \
  tests.test_ledger_service.LedgerServiceTests.test_github_login_accepts_dashboard_return_path \
  tests.test_ledger_service.LedgerServiceTests.test_github_callback_redirects_to_stored_claim_return_path
```

Expected: FAIL because no return cookie is set and callback redirects to `/dashboard`.

- [ ] **Step 3: Add return cookie and sanitizer**

In `ledger/main.py`, add near `OAUTH_STATE_COOKIE`:

```python
OAUTH_RETURN_COOKIE = "chief_ledger_oauth_return"
```

Add helper near `public_base_url()`:

```python
def safe_dashboard_return_path(value: str | None) -> str:
    if not value:
        return "/dashboard"
    text = str(value).strip()
    if not text.startswith("/dashboard"):
        return "/dashboard"
    if text.startswith("//") or "://" in text:
        return "/dashboard"
    return text
```

- [ ] **Step 4: Update login and callback routes**

Change `github_login` signature to:

```python
async def github_login(request: Request, returnTo: str = "") -> RedirectResponse:
```

Inside `github_login`, after setting `OAUTH_STATE_COOKIE`, set the return cookie:

```python
    response.set_cookie(
        OAUTH_RETURN_COOKIE,
        safe_dashboard_return_path(returnTo),
        httponly=True,
        samesite="lax",
        secure=public_base_url(request).startswith("https://"),
        max_age=600,
    )
```

Change `github_callback` signature to include:

```python
    stored_return: str | None = Cookie(default=None, alias=OAUTH_RETURN_COOKIE),
```

Change `complete_github_callback` signature to include:

```python
    stored_return: str | None = None,
```

In `complete_github_callback`, replace the success redirect target:

```python
    response = RedirectResponse(safe_dashboard_return_path(stored_return), status_code=307)
```

Before returning success response, delete the return cookie:

```python
    response.delete_cookie(OAUTH_RETURN_COOKIE)
```

Pass `stored_return=stored_return` from `github_callback` to `complete_github_callback`.

- [ ] **Step 5: Run login return tests and verify GREEN**

Run the command from Step 2.

Expected: PASS with `Ran 2 tests`.

- [ ] **Step 6: Run full ledger tests**

Run:

```bash
cd /Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger
PYTHONPATH=. /Users/freedom/cc/OntologyAgent/.venv/bin/python -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```bash
cd /Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest
git add ledger/main.py ledger/tests/test_ledger_service.py
git commit -m "feat: preserve dashboard claim return"
```

## Task 3: Add Dashboard Auto-Claim Deep Link Behavior

**Files:**
- Modify: `/Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger/web/dashboard.html`
- Test: `/Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger/tests/test_ledger_service.py`

- [ ] **Step 1: Write failing dashboard HTML test**

Extend `test_dashboard_serves_user_dashboard_page` or add a nearby test:

```python
    def test_dashboard_supports_claim_code_deep_link_auto_claim(self) -> None:
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("params.get('claimCode')", html)
        self.assertIn("params.get('agentId')", html)
        self.assertIn("returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}", html)
        self.assertIn("candidate.agentId === deepLinkAgentId", html)
        self.assertIn("window.history.replaceState({}, '', cleanUrl.toString())", html)
```

- [ ] **Step 2: Run dashboard HTML test and verify RED**

Run:

```bash
cd /Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger
PYTHONPATH=. /Users/freedom/cc/OntologyAgent/.venv/bin/python -m unittest \
  tests.test_ledger_service.LedgerServiceTests.test_dashboard_supports_claim_code_deep_link_auto_claim
```

Expected: FAIL because the dashboard still uses `params.get('claim')` and does not auto-claim.

- [ ] **Step 3: Parse canonical deep-link params**

In `ledger/web/dashboard.html`, change the app state URL parsing block from the old claim token to:

```javascript
    const claimToken = params.get('claimCode') || '';
    const deepLinkAgentId = params.get('agentId') || '';
```

Add `deepLinkAgentId` to the `window.useAppState()` provider value:

```javascript
      claimToken, deepLinkAgentId, internalMode,
```

- [ ] **Step 4: Redirect unauthenticated claim links to GitHub login**

In the auth-session `then((payload) => { ... })` branch, before `signOut()` for unauthenticated users, add:

```javascript
          if (claimToken && deepLinkAgentId) {
            const returnTo = encodeURIComponent(window.location.pathname + window.location.search);
            window.location.href = `/auth/github/login?returnTo=${returnTo}`;
            return;
          }
```

Update the effect dependency list to include `claimToken` and `deepLinkAgentId`.

- [ ] **Step 5: Auto-claim exact candidate after candidates load**

In `ClaimForm`, destructure `deepLinkAgentId`:

```javascript
    const { agents: claimedAgents, claimAgent, ownerEmail, currentUser, resetAll, claimToken, deepLinkAgentId } = window.useAppState();
```

After `demoCodes` calculation, add:

```javascript
    React.useEffect(() => {
      if (!claimToken || !deepLinkAgentId || status !== 'ready') return;
      const lowered = claimToken.trim().toLowerCase();
      const candidate = candidates.find((item) => (
        item.agentId === deepLinkAgentId &&
        String(item.claimCode || '').trim().toLowerCase() === lowered
      ));
      if (!candidate) {
        setCode(claimToken);
        setErrorKey('mvp.dash.claim.error_not_found');
        setStep('input');
        return;
      }
      if (claimedAgents.includes(candidate.agentId)) {
        claimAgent(candidate.agentId);
      } else {
        claimAgent(candidate.agentId);
        if (onClaimed) onClaimed(candidate.agentId);
      }
      const cleanUrl = new URL(window.location.href);
      cleanUrl.searchParams.delete('claimCode');
      cleanUrl.searchParams.delete('agentId');
      window.history.replaceState({}, '', cleanUrl.toString());
    }, [claimToken, deepLinkAgentId, status, candidates, claimedAgents, claimAgent, onClaimed]);
```

- [ ] **Step 6: Run dashboard HTML test and verify GREEN**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 7: Run full ledger tests**

Run:

```bash
cd /Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger
PYTHONPATH=. /Users/freedom/cc/OntologyAgent/.venv/bin/python -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

```bash
cd /Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest
git add ledger/web/dashboard.html ledger/tests/test_ledger_service.py
git commit -m "feat: auto-claim dashboard deep links"
```

## Task 4: Add `chief claim link`

**Files:**
- Modify: `/Users/freedom/cc/chief-install/bin/chief`
- Test: `/Users/freedom/cc/chief-install/tests/test_chief_transfer.py`

- [ ] **Step 1: Extend fake ledger server for claim link**

In `/Users/freedom/cc/chief-install/tests/test_chief_transfer.py`, add class variables:

```python
    posted_claims = []
```

Reset it in `setUp()`:

```python
        LedgerHandler.posted_claims = []
```

In `do_POST`, add before the fallback 404:

```python
        if self.path == "/ledger/claims/link":
            self.posted_claims.append(body)
            self._json(
                200,
                {
                    "agentId": body["agentId"],
                    "agentName": body["agentName"],
                    "ownerEmail": body["email"].lower(),
                    "claimCode": "clm_testclaim",
                    "claimUrl": "https://ledger.example.test/dashboard?claimCode=clm_testclaim&agentId=" + body["agentId"],
                    "agentUrl": "https://ledger.example.test/dashboard?agentId=" + body["agentId"],
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "circleWalletId": "circle-wallet-1",
                },
            )
            return
```

- [ ] **Step 2: Write failing `chief claim link` test**

Add:

```python
    def run_claim_link(self):
        return subprocess.run(
            [str(CHIEF), "claim", "link"],
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_claim_link_posts_openclaw_profile_and_prints_links(self):
        result = self.run_claim_link()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            LedgerHandler.posted_claims,
            [
                {
                    "agentId": "agent_sender",
                    "agentName": "Sender",
                    "email": "sender@example.com",
                    "agentDescription": "",
                }
            ],
        )
        self.assertIn("Agent ID:   agent_sender", result.stdout)
        self.assertIn("Claim Code: clm_testclaim", result.stdout)
        self.assertIn("Claim Link: https://ledger.example.test/dashboard?claimCode=clm_testclaim&agentId=agent_sender", result.stdout)
        self.assertIn("Agent Link: https://ledger.example.test/dashboard?agentId=agent_sender", result.stdout)
```

- [ ] **Step 3: Run claim test and verify RED**

Run:

```bash
cd /Users/freedom/cc/chief-install
python -m unittest tests.test_chief_transfer.ChiefTransferTests.test_claim_link_posts_openclaw_profile_and_prints_links
```

Expected: FAIL because `chief claim link` is not implemented.

- [ ] **Step 4: Add OpenClaw profile path helper**

In `bin/chief`, replace profile lookup helpers with OpenClaw-only lookup:

```sh
profile_path_for_openclaw() {
  if [ -n "${CHIEF_AGENT_PROFILE_PATH:-}" ]; then
    printf '%s\n' "$CHIEF_AGENT_PROFILE_PATH"
    return
  fi
  if [ -n "${OPENCLAW_WORKSPACE_DIR:-}" ]; then
    printf '%s/.eigenflux/servers/eigenflux/profile.json\n' "$OPENCLAW_WORKSPACE_DIR"
    return
  fi
  if [ -f "$PWD/.eigenflux/servers/eigenflux/profile.json" ]; then
    printf '%s/.eigenflux/servers/eigenflux/profile.json\n' "$PWD"
    return
  fi
  printf '%s/workspace/.eigenflux/servers/eigenflux/profile.json\n' "$PWD"
}

agent_profile_json() {
  profile_path="$(profile_path_for_openclaw)"
  if [ -f "$profile_path" ]; then
    cat "$profile_path"
    return
  fi
  printf '{}\n'
}
```

Keep existing `ledger_state_path_for_profile()` using `agent_profile_json()`.

- [ ] **Step 5: Add profile-to-claim JSON helper**

Add after `transfer_payload_from_email()`:

```sh
claim_link_payload_from_profile() {
  profile="$(agent_profile_json)"
  if command -v python3 >/dev/null 2>&1; then
    python3 -c '
import json
import sys

profile = json.loads(sys.argv[1])
agent_id = str(profile.get("agent_id") or profile.get("agentId") or "").strip()
agent_name = str(profile.get("agent_name") or profile.get("agentName") or agent_id).strip()
email = str(profile.get("email") or "").strip().lower()
description = str(profile.get("bio") or profile.get("agentDescription") or "").strip()
if not agent_id:
    raise SystemExit("current OpenClaw profile is missing agent_id")
if not email:
    raise SystemExit("current OpenClaw profile is missing email")
print(json.dumps({
    "agentId": agent_id,
    "agentName": agent_name,
    "email": email,
    "agentDescription": description,
}, separators=(",", ":")))
' "$profile"
    return
  fi

  agent_id="$(json_string_field agent_id "$profile")"
  if [ -z "$agent_id" ]; then
    agent_id="$(json_string_field agentId "$profile")"
  fi
  agent_name="$(json_string_field agent_name "$profile")"
  if [ -z "$agent_name" ]; then
    agent_name="$(json_string_field agentName "$profile")"
  fi
  if [ -z "$agent_name" ]; then
    agent_name="$agent_id"
  fi
  email="$(json_string_field email "$profile" | tr '[:upper:]' '[:lower:]')"
  description="$(json_string_field bio "$profile")"
  if [ -z "$description" ]; then
    description="$(json_string_field agentDescription "$profile")"
  fi
  if [ -z "$agent_id" ]; then
    echo "current OpenClaw profile is missing agent_id" >&2
    return 2
  fi
  if [ -z "$email" ]; then
    echo "current OpenClaw profile is missing email" >&2
    return 2
  fi
  printf '{"agentId":"%s","agentName":"%s","email":"%s","agentDescription":"%s"}\n' \
    "$(json_escape "$agent_id")" \
    "$(json_escape "$agent_name")" \
    "$(json_escape "$email")" \
    "$(json_escape "$description")"
}
```

If `json_escape()` is currently nested inside `transfer_payload_from_email`, move it to top level before both helpers:

```sh
json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}
```

- [ ] **Step 6: Add claim command**

Update `usage()` to include:

```text
  chief claim link
```

Add this `case` branch before `ledger)`:

```sh
  claim)
    case "${2:-}" in
      link)
        claim_body="$(claim_link_payload_from_profile)" || exit 2
        claim_response="$(post_json "$LEDGER_URL/ledger/claims/link" "${LEDGER_FALLBACK_URL:+$LEDGER_FALLBACK_URL/ledger/claims/link}" "$claim_body")"
        if command -v python3 >/dev/null 2>&1; then
          python3 -c '
import json
import sys

payload = json.loads(sys.argv[1])
print(f"Agent ID:   {payload.get(\"agentId\", \"\")}")
print(f"Claim Code: {payload.get(\"claimCode\", \"\")}")
print(f"Claim Link: {payload.get(\"claimUrl\", \"\")}")
print(f"Agent Link: {payload.get(\"agentUrl\", \"\")}")
' "$claim_response"
        else
          printf '%s\n' "$claim_response"
        fi
        ;;
      *)
        usage
        exit 2
        ;;
    esac
    ;;
```

- [ ] **Step 7: Run claim test and verify GREEN**

Run the command from Step 3.

Expected: PASS.

- [ ] **Step 8: Run all chief-install tests**

Run:

```bash
cd /Users/freedom/cc/chief-install
python -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 9: Commit Task 4 in `chief-install`**

```bash
cd /Users/freedom/cc/chief-install
git add bin/chief tests/test_chief_transfer.py
git commit -m "feat: add OpenClaw claim link command"
```

## Task 5: Make Installer OpenClaw-Only And Print Claim Links

**Files:**
- Modify: `/Users/freedom/cc/chief-install/install.sh`
- Modify: `/Users/freedom/cc/chief-install/INSTALL.md`
- Create: `/Users/freedom/cc/chief-install/tests/test_install_openclaw.py`

- [ ] **Step 1: Write failing installer tests**

Create `/Users/freedom/cc/chief-install/tests/test_install_openclaw.py`:

```python
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"


class OpenClawInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        for name in ["runtime-openclaw-x", "runtime-openclaw-y"]:
            workspace = self.root / name / "workspace"
            profile = workspace / ".eigenflux" / "servers" / "eigenflux" / "profile.json"
            profile.parent.mkdir(parents=True)
            profile.write_text(
                '{"email":"owner@example.com","agent_id":"' + name + '","agent_name":"' + name + '"}',
                encoding="utf-8",
            )

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_install(self, extra_env=None):
        env = {**os.environ, "CHIEF_LEDGER_HTTP_URL": "http://127.0.0.1:9"}
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(INSTALL)],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_installs_into_all_openclaw_workspaces_and_keeps_link_failure_nonfatal(self):
        result = self.run_install()

        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ["runtime-openclaw-x", "runtime-openclaw-y"]:
            workspace = self.root / name / "workspace"
            self.assertTrue((workspace / ".local" / "bin" / "chief").exists())
            self.assertTrue((workspace / "skills" / "chief-ledger" / "SKILL.md").exists())
            self.assertTrue((workspace / "skills" / "chief-a2a-service-trade" / "SKILL.md").exists())
            self.assertIn(f"OPENCLAW_WORKSPACE_DIR={workspace}", result.stdout)
        self.assertIn("Claim link unavailable", result.stdout)

    def test_explicit_openclaw_workspace_installs_only_that_workspace(self):
        target = self.root / "runtime-openclaw-x" / "workspace"
        result = self.run_install({"OPENCLAW_WORKSPACE_DIR": str(target)})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((target / ".local" / "bin" / "chief").exists())
        other = self.root / "runtime-openclaw-y" / "workspace"
        self.assertFalse((other / ".local" / "bin" / "chief").exists())

    def test_install_fails_when_no_openclaw_workspace_exists(self):
        shutil.rmtree(self.root / "runtime-openclaw-x")
        shutil.rmtree(self.root / "runtime-openclaw-y")

        result = self.run_install()

        self.assertEqual(result.returncode, 2)
        self.assertIn("No OpenClaw workspace found", result.stderr)
```

- [ ] **Step 2: Run installer tests and verify RED**

Run:

```bash
cd /Users/freedom/cc/chief-install
python -m unittest tests.test_install_openclaw
```

Expected: FAIL because installer still uses old runtime defaults and does not print OpenClaw retry commands.

- [ ] **Step 3: Rewrite workspace discovery**

In `install.sh`, replace runtime variables:

```bash
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
```

Add:

```bash
discover_workspaces() {
  if [[ -n "${OPENCLAW_WORKSPACE_DIR:-}" ]]; then
    printf '%s\n' "$OPENCLAW_WORKSPACE_DIR"
    return
  fi

  local found=0
  for workspace in "$PWD"/runtime-openclaw-*/workspace; do
    if [[ -d "$workspace" ]]; then
      found=1
      printf '%s\n' "$workspace"
    fi
  done

  if [[ "$found" -eq 0 ]]; then
    echo "No OpenClaw workspace found. Set OPENCLAW_WORKSPACE_DIR=/path/to/workspace." >&2
    return 2
  fi
}
```

- [ ] **Step 4: Install per workspace**

Replace single-destination install body with:

```bash
install_workspace() {
  local workspace="$1"
  local skills_dest="$workspace/skills"
  local bin_dest="$workspace/.local/bin"

  mkdir -p "$skills_dest" "$bin_dest"
  find "$skills_dest" -maxdepth 1 -type d -name 'chief-*' -exec rm -rf {} +

  install_skill_to "$skills_dest" chief-ledger
  install_skill_to "$skills_dest" chief-a2a-service-trade

  install_file "bin/chief" "$bin_dest/chief"
  chmod +x "$bin_dest/chief"

  cat <<EOF
Chief installed successfully.

OpenClaw workspace: $workspace
CLI:                $bin_dest/chief
Skills:             $skills_dest
EOF

  if OPENCLAW_WORKSPACE_DIR="$workspace" "$bin_dest/chief" claim link; then
    return 0
  fi

  cat <<EOF
Claim link unavailable for $workspace.
Retry:
OPENCLAW_WORKSPACE_DIR=$workspace $bin_dest/chief claim link
EOF
}

while IFS= read -r workspace; do
  install_workspace "$workspace"
done < <(discover_workspaces)
```

Keep `install_skill_to()` and `install_file()` helpers. Remove old legacy cleanup paths because they are ZeroClaw-only.

- [ ] **Step 5: Update install docs**

In `INSTALL.md`, replace old runtime wording with:

````markdown
Chief installs into OpenClaw workspaces only.

By default, run the installer from the directory that contains `runtime-openclaw-*/workspace`:

```bash
curl -fsSL https://raw.githubusercontent.com/arthurxuwei/chief-install/main/install.sh | bash
```

To install one workspace explicitly:

```bash
OPENCLAW_WORKSPACE_DIR=/path/to/runtime-openclaw-x/workspace \
curl -fsSL https://raw.githubusercontent.com/arthurxuwei/chief-install/main/install.sh | bash
```

After installation, the installer attempts to print `Claim Link` and `Agent Link`.
If the ledger is unavailable, rerun:

```bash
OPENCLAW_WORKSPACE_DIR=/path/to/workspace /path/to/workspace/.local/bin/chief claim link
```
````

- [ ] **Step 6: Run installer tests and verify GREEN**

Run:

```bash
cd /Users/freedom/cc/chief-install
python -m unittest tests.test_install_openclaw
```

Expected: PASS.

- [ ] **Step 7: Run all chief-install tests**

Run:

```bash
cd /Users/freedom/cc/chief-install
python -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 8: Commit Task 5 in `chief-install`**

```bash
cd /Users/freedom/cc/chief-install
git add install.sh INSTALL.md tests/test_install_openclaw.py
git commit -m "feat: install Chief into OpenClaw workspaces"
```

## Task 6: Final Verification And Scan

**Files:**
- Modify only if verification finds issues.

- [ ] **Step 1: Run ledger tests**

```bash
cd /Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger
PYTHONPATH=. /Users/freedom/cc/OntologyAgent/.venv/bin/python -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 2: Run chief-install tests**

```bash
cd /Users/freedom/cc/chief-install
python -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 3: Run active MCP scan in `OntologyAgent`**

```bash
cd /Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest
rg -n "MCP|mcp|mcp_|Mcp|FastMCP|streamable_http|chain-mcp|circle-mcp|/mcp|CHAIN_MCP|CIRCLE_MCP|LEDGER_.*MCP|@modelcontextprotocol" --glob '!docs/superpowers/**' .
```

Expected: no matches.

- [ ] **Step 4: Run MCP scan in `chief-install`**

```bash
cd /Users/freedom/cc/chief-install
rg -n "MCP|mcp|/mcp|LEDGER_.*MCP|CHIEF_.*MCP" .
```

Expected: no matches.

- [ ] **Step 5: Confirm git status**

```bash
cd /Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest && git status --short
cd /Users/freedom/cc/chief-install && git status --short
```

Expected: clean or only intentional uncommitted changes if the user asks not to commit.

- [ ] **Step 6: Commit final docs or fixes if needed**

If Task 6 required edits, commit them in the relevant repository:

```bash
git add <changed-files>
git commit -m "chore: verify OpenClaw claim links"
```

If no edits were needed, do not create an empty commit.

## Dependency Order

1. Task 1 must land before `chief claim link` can call a real endpoint.
2. Task 2 must land before unauthenticated claim links can return to their original URL.
3. Task 3 depends on Task 2 for login return and on existing `/dashboard/claimable-agents`.
4. Task 4 can be implemented after Task 1 because it posts to `/ledger/claims/link`.
5. Task 5 depends on Task 4 because the installer calls `chief claim link`.
6. Task 6 runs after all implementation tasks.

## Rollback Notes

- Dashboard auto-claim is local browser state only; reverting Task 3 returns to manual claim-code input.
- `chief-install` OpenClaw-only changes intentionally remove old install layout support. Reintroducing old runtime support would be a separate feature, not a rollback requirement.
- `/ledger/claims/link` is additive and does not change existing wallet or dashboard endpoints.
