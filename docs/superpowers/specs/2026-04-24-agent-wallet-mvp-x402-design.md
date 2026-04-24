# Agent Wallet MVP x402 Design

## Summary

This spec defines the first end-to-end `Agent Wallet MVP` implementation for the current `OntologyAgent` repository.

The goal is not to build the full product described in the earlier requirements document. The goal is to produce a working, demoable, test-chain-only closed loop that reuses the existing repository architecture as much as possible:

- `agent` remains the user-facing web and orchestration layer
- `chain` remains the MCP skill provider for wallet and chain-side capabilities
- `x402-seller` remains the paid service surface for Agent-to-Agent service calls

The resulting MVP should let a user:

1. sign in with GitHub OAuth
2. create an Agent Wallet backed by Circle sandbox
3. claim that wallet with a one-time claim code
4. inspect wallet and ownership state in the web UI
5. register an x402-priced Agent service that pays to the Circle wallet address
6. trigger an A2A x402 payment flow on `Base Sepolia`
7. inspect the resulting settlement and ledger trail

This MVP intentionally uses the strongest available real integrations and only falls back to local state where the current environment has no usable external dependency.

## Current Context

The repository already contains the right architectural pieces for a minimal but real implementation:

- `agent/main.py` already hosts the main FastAPI app, session flow, and current web console
- `agent/web/chat.html` already provides a single-page operator interface and can be extended without introducing a new front-end stack
- `chain` already exposes MCP tools for wallet inspection, x402 buyer flow, normal execution, and user operations
- `x402-seller` already implements a standard x402 seller flow on `Base Sepolia` and is already wired into `docker-compose.yml`

The repository also already assumes the following constraints:

- chain capabilities should be exposed as MCP tools, not as ad hoc HTTP logic inside `agent`
- test-chain usage is already centered around `Base Sepolia`
- x402 is already present as a first-class paid-resource protocol in the repo

The missing pieces are not general plumbing. The missing pieces are product-shaped wallet behaviors:

- Circle-backed Agent Wallet creation
- Owner identity and claim flow
- a persistent local state model for Owners, Agents, Wallets, Claims, and payment history
- a UI flow that explains and demonstrates the product instead of only exposing generic chat and observability

## Product Decision Summary

The following decisions are locked in for this spec.

### A2A settlement path

The first A2A path will use `x402`.

That means the initial MVP A2A transaction is modeled as:

- Agent A requests a paid HTTP service from Agent B
- Agent B responds with `402 Payment Required`
- Agent A pays through x402
- Agent B returns the paid result

This is intentionally narrower than a general-purpose escrow marketplace. It is the shortest real path to a functioning A2A payment demo on a test chain.

### Payer and payee model

The first version uses the selected hybrid model:

- the x402 buyer uses the existing local test private key flow already present in `chain`
- the x402 seller receives funds at a Circle-created Agent Wallet address

This means the seller wallet is a real Circle wallet, while the buyer remains the current local x402 buyer signer for the first version.

This is the fastest path to a real, testable flow while preserving a migration path toward Circle-backed payer signing later.

### Claim model

Claim is real, not simulated.

That means:

- every created Agent Wallet gets a one-time claim code
- claim codes are stored only as hashes
- claim status is persistent
- claim transitions are enforced by the backend
- ownership is bound to a real OAuth identity

### OAuth model

The first version uses GitHub OAuth only.

This is enough for a real Owner identity system and best fits the repository’s target audience of developers.

## Scope

### Included

- GitHub OAuth login for the web app
- persistent Owner records
- Circle sandbox Agent Wallet creation
- one-time claim code generation and consumption
- claimed vs unclaimed wallet state
- persistent Agent Wallet state stored locally
- MCP tools in `chain` for Agent Wallet operations
- x402 service registration using a Circle wallet address as seller payee
- x402 A2A service invocation using the existing buyer signer path
- a web walkthrough inside the existing `agent` console
- test coverage for wallet state, claim flow, OAuth session handling, and x402 orchestration boundaries

### Excluded

- Coinbase Onramp
- MoonPay
- EigenFlux integration
- Google OAuth
- Email OTP
- mobile app
- multi-chain support
- production escrow
- dispute handling or refunds
- Circle-backed x402 buyer signing
- generalized Agent discovery marketplace

## Design Goals

- maximize reuse of the current architecture
- keep wallet abilities exposed as Agent skills through MCP tools
- keep all chain-side actions on `Base Sepolia`
- prefer real external integrations whenever credentials already exist
- avoid introducing a new web framework or a new service unless the current topology cannot support the feature
- keep the first version explainable in a single-page demo flow
- preserve a clean migration path to a richer wallet product later

## Recommended Approach

Three approaches were considered.

1. add wallet features directly into `agent` and bypass MCP
2. create a separate `agent-wallet-demo` service with its own API
3. extend `chain` with Agent Wallet MCP tools and use `agent` as the orchestration and UI layer

The recommended approach is option 3.

This matches the repository’s existing separation of concerns:

- `chain` owns chain-side and wallet-adjacent capabilities
- `agent` owns user flows, sessions, UI, and orchestration

It also satisfies the product requirement that wallet ability should exist as an Agent skill rather than as a hidden backend-only implementation detail.

## Architecture Overview

The system is split into five slices.

### 1. Owner and session slice (`agent`)

This slice handles:

- GitHub OAuth redirect
- OAuth callback
- owner creation or lookup
- session cookie management
- exposing the current signed-in owner to the UI

This slice does not know how to create Circle wallets directly. It only coordinates identity and ownership.

### 2. Wallet state and claim slice (`agent`)

This slice handles:

- local persistence of Owners, Agents, Wallets, Claim records, and payment history
- claim code generation and hashing
- claim validation and state transition
- joining local product state with data returned by `chain` MCP tools

This slice is local application state, not chain execution logic.

### 3. Agent Wallet skill slice (`chain`)

This slice exposes new MCP tools such as:

- `agent_wallet_init`
- `agent_wallet_status`
- `agent_wallet_register_x402_service`
- `agent_wallet_call_x402_service`

This slice wraps:

- Circle sandbox wallet creation
- Circle wallet inspection where needed
- x402 buyer flow reuse
- service registration rules needed for the demo

This is the core skill surface the Agent can discover and call.

### 4. Paid service slice (`x402-seller`)

This slice remains the seller-facing x402 service surface.

For this MVP it will be extended from a generic demo resource into at least one named Agent service endpoint that:

- presents a real x402 challenge
- settles through the existing facilitator flow
- uses a Circle Agent Wallet address as `payTo`
- returns a structured service result that can be displayed in the UI

### 5. Demo UI slice (`agent/web/chat.html`)

This slice extends the current console page with a guided `Agent Wallet MVP` panel.

The panel should coexist with the current chat and observability features. It should not replace them.

## Real vs Local Boundaries

The implementation must be explicit about what is real and what is local.

### Real integrations

- GitHub OAuth
- Circle sandbox wallet creation
- x402 buyer flow
- x402 seller challenge and settlement
- `Base Sepolia` network and asset settings
- real wallet addresses and real test-chain settlement metadata

### Local persistent state

- Owner records
- Agent records
- claim code hashes
- claimed/unclaimed status
- service registry entries used by the demo UI
- payment history snapshots shown in the product UI

### Explicitly not faked

The system must not pretend that a real Circle or x402 action happened if it did not.

If required credentials are missing or a provider call fails, the UI must show a real error state and preserve the current local state.

## User Flow

The MVP walkthrough in the web UI should follow this exact order.

### Step 1: Sign in with GitHub

The user selects `Sign in with GitHub`.

The browser is redirected to GitHub OAuth. On callback:

- the backend exchanges the code for an access token
- the backend fetches GitHub user information
- the backend creates or reuses an `Owner`
- the backend writes an authenticated session cookie

The page then shows the signed-in owner identity.

### Step 2: Create Agent Wallet

The user creates a new Agent Wallet demo entity.

The backend calls the `agent_wallet_init` MCP tool. The tool:

- creates a Circle sandbox wallet for the Agent
- returns the Circle wallet identifiers and address

The `agent` backend then:

- creates a local `Agent`
- creates a local `WalletRecord`
- creates a one-time claim record
- stores only the claim hash
- returns the wallet address and one-time claim code to the UI

The wallet starts in `unclaimed` state.

### Step 3: Claim Wallet

The signed-in user submits the claim code.

The backend:

- verifies the current session owner
- hashes the submitted code
- compares it with the stored hash
- checks expiry
- checks the wallet is still `unclaimed`

On success:

- the wallet becomes `claimed`
- the owner is bound to the wallet
- the claim code is consumed and cannot be reused

### Step 4: Register an x402 service

The user registers a demo paid service for the claimed Agent.

This uses the Circle wallet address as the service `payTo` address and creates a local service registration record.

The service is then available for the next step.

### Step 5: Trigger A2A x402 payment

The user triggers a demo service call from the UI.

The backend calls `agent_wallet_call_x402_service`, which:

- looks up the service definition
- calls the target x402 seller endpoint
- receives `402 Payment Required`
- reuses the existing x402 buyer flow in `chain`
- pays with the configured x402 buyer private key
- retries the request with the payment signature
- receives the paid service result

The backend stores the payment attempt and displays:

- service name
- price
- seller payee address
- settlement status
- any tx hash or facilitator response metadata returned

### Step 6: Inspect wallet and payment history

The UI shows:

- owner identity
- claim status
- Circle wallet address
- latest known wallet status
- registered services
- x402 payment history

## Data Model

The first version should use a local JSON state file managed by `agent`.

Recommended path:

- `agent/data/agent_wallet_state.json`

The state should be represented in Python as structured models, not as free-form dictionaries.

### Owner

Fields:

- `ownerId`
- `provider` (`github`)
- `providerUserId`
- `login`
- `email`
- `displayName`
- `avatarUrl`
- `createdAt`
- `updatedAt`

### Agent

Fields:

- `agentId`
- `name`
- `description`
- `ownerId` nullable
- `walletId`
- `walletAddress`
- `claimStatus` (`unclaimed` or `claimed`)
- `createdAt`
- `updatedAt`

### ClaimRecord

Fields:

- `claimId`
- `agentId`
- `claimCodeHash`
- `expiresAt`
- `claimedAt` nullable
- `consumedByOwnerId` nullable
- `createdAt`

The claim code itself must never be persisted in plaintext after response generation.

### ServiceRegistration

Fields:

- `serviceId`
- `agentId`
- `name`
- `path`
- `priceAtomic`
- `assetAddress`
- `network`
- `payTo`
- `active`
- `createdAt`

### PaymentRecord

Fields:

- `paymentId`
- `serviceId`
- `buyerKind` (`local_x402_buyer`)
- `sellerAgentId`
- `sellerWalletAddress`
- `amountAtomic`
- `assetAddress`
- `network`
- `status`
- `requestUrl`
- `resultSummary`
- `txHash` nullable
- `settlementReference` nullable
- `createdAt`

## MCP Tool Design

The new wallet features should be exposed from `chain` as MCP tools.

### `agent_wallet_init`

Purpose:

- create a Circle sandbox Agent Wallet

Inputs:

- `agentName`
- `agentDescription` optional

Outputs:

- Circle wallet id
- wallet set id if returned
- blockchain
- wallet address

Behavior:

- no local owner logic
- no claim logic
- pure wallet provisioning responsibility

### `agent_wallet_status`

Purpose:

- return the current wallet-side status for a known wallet address or Circle wallet id

Inputs:

- `walletAddress` optional
- `circleWalletId` optional

Outputs:

- wallet address
- blockchain
- Circle identifiers
- any balance or status fields the implementation can reliably fetch

### `agent_wallet_register_x402_service`

Purpose:

- validate and return the normalized x402 service registration payload for an Agent wallet

Inputs:

- `name`
- `path`
- `priceAtomic`
- `payTo`

Outputs:

- normalized service payload

This tool should keep x402-related normalization in `chain`, not scattered through `agent`.

### `agent_wallet_call_x402_service`

Purpose:

- call a paid service through the existing x402 buyer flow

Inputs:

- `url`
- `method`
- `headers` optional
- `body` optional

Outputs:

- upstream result
- payment requirements
- selected payment
- settlement response
- policy decision

This should be a thin wrapper around the existing `chain_x402_fetch` behavior with a more product-specific name and response shape.

## Circle Integration Design

Circle is used only for seller wallet provisioning in the first version.

Responsibilities:

- create developer-controlled wallets on Circle sandbox
- return a stable wallet address for use as seller `payTo`

The first version does not require Circle to sign x402 buyer payloads. That comes later.

### Why this boundary is correct

The repository already has a working x402 buyer flow driven by a local private key. Replacing that immediately with Circle signing would significantly expand scope and delay the first real A2A demo.

Using Circle for seller wallet provisioning still makes the wallet product real:

- the user owns and claims a real Circle wallet
- the seller endpoint points to that real wallet address
- the A2A payment path is still real and test-chain-based

## OAuth and Session Design

GitHub OAuth is implemented in `agent`.

### Required endpoints

- `GET /auth/github/login`
- `GET /auth/github/callback`
- `GET /auth/session`
- `POST /auth/logout`

### Session behavior

- use an `HttpOnly` cookie
- sign the cookie using `AUTH_SESSION_SECRET`
- use a server-side session store or signed cookie payload carrying only a session identifier
- the current session must be enough to resolve the current Owner

### Claim dependency

`POST /agent-wallet/claim` must reject unauthenticated requests.

## HTTP API Design in `agent`

The following product endpoints should be added to `agent`.

### `GET /agent-wallet/state`

Returns the joined product view for the current signed-in owner:

- owner
- owned agents
- claim status
- services
- payment history

### `POST /agent-wallet/init`

Creates a new Agent Wallet by:

- calling `agent_wallet_init`
- creating local Agent and Claim records
- returning the newly generated one-time claim code

### `POST /agent-wallet/claim`

Consumes a claim code for the signed-in owner.

### `POST /agent-wallet/register-service`

Creates a local service registration using the Agent’s Circle wallet address as `payTo`.

### `POST /agent-wallet/call-service`

Triggers an x402-paid service call and persists the result in local history.

### `POST /agent-wallet/reset`

Resets only the local demo state file.

This endpoint is acceptable because the feature is explicitly a local test/demo MVP.

## UI Design

The existing page in `agent/web/chat.html` should gain a dedicated `Agent Wallet MVP` panel without breaking current observability or chat use.

### Layout

The new panel should contain:

- current owner identity and auth status
- wallet creation card
- claim card
- service registration card
- x402 service call card
- wallet and payment history summary

### Display principles

- each step should clearly indicate whether it is real and configured
- missing GitHub or Circle configuration should be shown as blocking setup issues
- `claimed` vs `unclaimed` should be visually obvious
- x402 payments should surface `payTo`, amount, network, and settlement state

### UX constraints

- do not force the user to use the chat box for the guided flow
- allow the flow to be replayed from the page after reset
- keep operator observability and chat still available elsewhere on the page

## Error Handling

The MVP must fail loudly and specifically.

### GitHub OAuth errors

Examples:

- missing client id or secret
- state mismatch
- callback token exchange failure
- user info fetch failure

User-facing behavior:

- show a clear auth error
- do not create partial Owner records

### Circle errors

Examples:

- missing API key or entity secret
- wallet creation failure
- unsupported blockchain config

User-facing behavior:

- local Agent record should not be created if wallet provisioning failed
- UI should show that wallet creation failed before any claim code exists

### Claim errors

Examples:

- not authenticated
- wrong code
- expired code
- already claimed

User-facing behavior:

- return precise claim failure reason
- preserve current state

### x402 payment errors

Examples:

- missing buyer signer key
- 402 malformed response
- facilitator verify or settle failure
- seller service error after payment challenge

User-facing behavior:

- create a failed payment history record
- keep the service registration intact
- display the upstream failure and settlement reason if available

## Security and Safety

Even though this is a test-chain MVP, the implementation must preserve sound safety boundaries.

### Claim safety

- store only claim hashes
- make claim codes single-use
- enforce expiry
- require authenticated Owner session for claim

### OAuth safety

- use `state`
- use PKCE
- use secure cookie settings when not on localhost
- do not trust callback query values without server-side exchange

### Provider credentials

- never expose Circle or GitHub secrets to the browser
- never commit secrets into the repo

### x402 spending safety

- continue reusing the existing x402 policy guard
- keep network and asset restricted to Base Sepolia USDC
- preserve the current single and daily cap enforcement

## Testing Strategy

The first version must be implemented test-first where behavior is practical to cover.

### `agent` tests

- OAuth session endpoint behavior
- unauthenticated claim rejection
- successful claim transition
- invalid and expired claim rejection
- state endpoint shape
- service registration persistence
- x402 call result persistence

### `chain` tests

- new MCP tool registration
- Circle wallet tool request validation
- x402 wrapper tool response normalization

### `x402-seller` tests

- seller endpoint exposes correct payment challenge
- seller endpoint uses configured Circle wallet `payTo`
- successful settlement returns the expected service body

### Integration expectations

The repository should be able to run a local docker-compose demo in which:

- `agent` serves the UI
- `chain` serves MCP tools
- `x402-seller` serves the paid endpoint
- the UI walkthrough works against real GitHub OAuth and Circle sandbox credentials when provided

## Operational Configuration

The MVP expects the following runtime configuration.

### GitHub OAuth

- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `AUTH_SESSION_SECRET`
- `PUBLIC_BASE_URL`

### Circle

- `CIRCLE_API_KEY`
- `CIRCLE_ENTITY_SECRET` or equivalent supported by the chosen client implementation
- Circle blockchain target set to `BASE-SEPOLIA`

### x402

- `X402_BUYER_PRIVATE_KEY`
- `X402_FACILITATOR_URL`
- `X402_NETWORK=eip155:84532`
- `X402_USDC_ASSET_ADDRESS=0x036CbD53842c5426634e7929541eC2318f3dCF7e`

## Future Evolution

This design intentionally leaves room for the next major upgrade steps:

- replace local x402 buyer signing with a Circle-backed signer
- extend the seller registry into EigenFlux-backed discovery
- add email or Google login providers
- add true deposit and withdrawal flows
- introduce escrow for asynchronous A2A jobs

These are future steps, not hidden requirements of this MVP.

## References

- Circle Developer-Controlled Wallets quickstart: https://developers.circle.com/wallets/dev-controlled/create-your-first-wallet
- Circle create wallet API: https://developers.circle.com/api-reference/wallets/developer-controlled-wallets/create-wallet
- Circle developer transaction transfer API: https://developers.circle.com/api-reference/wallets/developer-controlled-wallets/create-developer-transaction-transfer
- Circle signing APIs: https://developers.circle.com/wallets/signing-apis
- GitHub OAuth web application flow: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
- Coinbase x402 overview: https://docs.cdp.coinbase.com/x402/welcome
- Coinbase x402 how it works: https://docs.cdp.coinbase.com/x402/core-concepts/how-it-works
- Base x402 Agent payments guide: https://docs.base.org/ai-agents/payments/pay-for-services-with-x402
