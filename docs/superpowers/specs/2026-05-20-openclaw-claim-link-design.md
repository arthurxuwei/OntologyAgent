# OpenClaw Claim Link Design

## Goal

After an OpenClaw agent installs Chief, the install flow should show the user two usable links:

- a claim link containing both `claimCode` and `agentId`
- an agent link containing `agentId`

When the user opens the claim link, the Chief dashboard should claim the agent for the logged-in user. If the user is not logged in, the dashboard should send them through GitHub login and return to the same claim link afterward.

This feature is OpenClaw-only. The installer does not preserve ZeroClaw runtime compatibility.

## Current Context

Chief is split across two repositories:

- `OntologyAgent` owns the ledger service and dashboard.
- `chief-install` owns the installer, Chief CLI, and OpenClaw skills.

The ledger already has the core pieces needed for claim:

- `claim_code_for_account(account, owner_email)` creates deterministic `clm_...` codes.
- `/dashboard/claimable-agents` lists unclaimed accounts scoped by owner email.
- The dashboard claim form can validate a claim code after GitHub login.
- GitHub auth is served by `/auth/github/login`, `/auth/github/callback`, and `/auth/session`.

The missing pieces are:

- an install-time way to create or reuse the agent wallet and print a claim URL
- a REST endpoint that returns claim metadata for an agent profile
- dashboard URL handling for `claimCode + agentId`
- preserving the claim URL through login
- automatic claim once the user is authenticated
- OpenClaw-only runtime discovery in `chief-install`

## Non-Goals

- Do not support old `runtime/workspace` or `/zeroclaw-data` install layouts.
- Do not keep `ZEROCLAW_RUNTIME_DIR` as the primary installer path.
- Do not preserve the old dashboard `claim` URL parameter for this OpenClaw-only flow.
- Do not add a new authentication provider.
- Do not create an on-chain claim transaction.
- Do not change the existing deterministic claim-code algorithm unless tests show a collision or security issue.
- Do not make installation fail just because the hosted ledger is temporarily unreachable.

## OpenClaw Runtime Model

The installer treats OpenClaw workspaces as the only supported target.

Default discovery:

- scan the current directory for `runtime-openclaw-*/workspace`
- install into every discovered workspace
- if no workspace is discovered, exit with a clear OpenClaw-only error and explain `OPENCLAW_WORKSPACE_DIR`

Explicit target:

- `OPENCLAW_WORKSPACE_DIR=/path/to/workspace` installs only that workspace

Installed files:

- `workspace/.local/bin/chief`
- `workspace/skills/chief-ledger/SKILL.md`
- `workspace/skills/chief-a2a-service-trade/SKILL.md`

Profile source:

- `workspace/.eigenflux/servers/eigenflux/profile.json`

Required profile fields for claim link generation:

- `agent_id` or `agentId`
- `agent_name` or `agentName`
- `email`

Optional profile fields:

- `bio` as `agentDescription`

If a workspace has no profile or lacks required fields, installation still succeeds but prints a clear warning and the command the user can run later.

## Chief CLI Additions

Add a new command:

```bash
chief claim link
```

It reads the OpenClaw profile for the current workspace, calls the ledger claim-link endpoint, and prints:

```text
Agent ID:   <agentId>
Claim Code: <claimCode>
Claim Link: <claimUrl>
Agent Link: <agentUrl>
```

The command supports:

- `CHIEF_LEDGER_URL` or `CHIEF_LEDGER_HTTP_URL` for the ledger base URL
- `CHIEF_AGENT_PROFILE_PATH` for tests or advanced manual override
- `OPENCLAW_WORKSPACE_DIR` to find the profile without relying on current working directory

Profile lookup order:

1. `CHIEF_AGENT_PROFILE_PATH`
2. `$OPENCLAW_WORKSPACE_DIR/.eigenflux/servers/eigenflux/profile.json`
3. `$PWD/.eigenflux/servers/eigenflux/profile.json`
4. `$PWD/workspace/.eigenflux/servers/eigenflux/profile.json`

The command does not use old ZeroClaw fallback paths.

## Installer Behavior

`install.sh` installs Chief into OpenClaw workspaces and then attempts claim-link generation per workspace.

For each workspace:

1. install Chief CLI
2. install Chief skills
3. run the installed `chief claim link` with `OPENCLAW_WORKSPACE_DIR=<workspace>`
4. print the returned links

If `chief claim link` fails because the profile is missing or the ledger is unreachable:

- keep the installation successful
- print a warning
- print the later retry command:

```bash
OPENCLAW_WORKSPACE_DIR=<workspace> <workspace>/.local/bin/chief claim link
```

This keeps installation useful for offline or first-boot cases.

## Ledger Endpoint

Add:

```http
POST /ledger/claims/link
```

Request:

```json
{
  "agentId": "312586087945994240",
  "agentName": "OpenClaw OntologyAgent",
  "email": "owner@example.com",
  "agentDescription": "optional profile bio"
}
```

Behavior:

1. normalize email
2. call the existing Agent Wallet get-or-create path
3. bind or reuse the ledger account
4. compute `claimCode` with `claim_code_for_account`
5. build dashboard URLs from `PUBLIC_BASE_URL` if configured, otherwise `https://ledger.curawealth.ai`

Response:

```json
{
  "agentId": "312586087945994240",
  "agentName": "OpenClaw OntologyAgent",
  "ownerEmail": "owner@example.com",
  "claimCode": "clm_...",
  "claimUrl": "https://ledger.curawealth.ai/dashboard?claimCode=clm_...&agentId=312586087945994240",
  "agentUrl": "https://ledger.curawealth.ai/dashboard?agentId=312586087945994240",
  "walletAddress": "0x...",
  "circleWalletId": "..."
}
```

Errors:

- `400` if required profile fields are missing
- existing wallet/ledger errors from get-or-create should map through the existing HTTP error helper

## Dashboard Deep Link Flow

The dashboard recognizes:

- `claimCode`
- `agentId`

The dashboard does not need to preserve the old `claim` alias for this feature.

Unauthenticated flow:

1. user opens `/dashboard?claimCode=...&agentId=...`
2. dashboard sees no authenticated session
3. dashboard sends the user to GitHub login with a return target
4. callback redirects back to the original dashboard URL
5. dashboard resumes claim

Authenticated flow:

1. dashboard loads `/dashboard/claimable-agents?email=<owner>&claimed=<local ids>`
2. find a candidate where both `claimCode` and `agentId` match
3. call existing local `claimAgent(agentId)`
4. set active agent to `agentId`
5. strip sensitive claim parameters from the browser URL after success
6. render the claimed agent dashboard

If the candidate is not found:

- show the claim form with an error
- keep the code prefilled so the user can inspect or retry

## Security And Privacy

- Claim links contain a bearer-like claim code and should be treated as sensitive.
- The dashboard strips `claimCode` from the URL after successful claim to reduce accidental sharing.
- Claim is scoped by the authenticated user's email because `/dashboard/claimable-agents` only returns accounts whose ledger email matches the session owner email.
- Installation output may still expose the claim link in terminal history or logs. This is acceptable for the current local OpenClaw install flow and should be documented.

## Testing

### `chief-install`

Add or update tests for:

- OpenClaw workspace discovery finds `runtime-openclaw-*/workspace`
- installer installs `chief` and skills under each OpenClaw workspace
- `chief claim link` reads profile fields and posts to `/ledger/claims/link`
- `chief claim link` prints claim and agent links
- installer succeeds when link generation fails and prints a retry command
- no test depends on old ZeroClaw paths

### `OntologyAgent` Ledger

Add tests for:

- `/ledger/claims/link` creates or reuses wallet/account and returns claim metadata
- response URLs include `claimCode` and `agentId`
- invalid request returns `400`
- GitHub login preserves dashboard return URL for claim links
- dashboard HTML includes `claimCode` parsing and auto-claim flow hooks

### Verification

Run:

```bash
cd /Users/freedom/cc/chief-install && python -m unittest discover -s tests
cd /Users/freedom/cc/OntologyAgent/.worktrees/remove-mcp-rest/ledger && PYTHONPATH=. /Users/freedom/cc/OntologyAgent/.venv/bin/python -m unittest discover -s tests
```

Also run the existing active-file MCP scan after implementation to keep the repository REST-only.

## Open Questions Resolved

- Installation may call the hosted ledger immediately.
- Only OpenClaw is supported.
- Claim should be automatic after login when the URL contains both `claimCode` and `agentId`.
