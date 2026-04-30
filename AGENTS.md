# OntologyAgent Agent Guidelines

## Essential Commands
- Start all services: `docker compose up -d --build`
- Worktree docker compose: `docker compose --env-file "$(dirname "$(git rev-parse --git-common-dir)")/.env" up -d`
- Agent chat: `./scripts/agent-chat.sh`
- Chain MCP demo: `./scripts/demo-chain-mcp.sh`
- Freqtrade MCP demo: `./scripts/demo-freqtrade-mcp.sh`
- Live x402 test: `PRIVATE_KEY=0x... ./scripts/live-x402-simplescraper.sh`
- Ledger tests: `cd ledger && python -m unittest discover -s tests`
- Ledger service build: `docker compose build ledger`

## Critical Architecture Notes
- Chain actions ONLY via chain MCP tools (signing, execution, UserOperations, x402 fetch)
- Trading/quant actions ONLY via Freqtrade MCP tools (start/stop bot, evaluate signal, force trades)
- Offchain balances and Escrow state live in the standalone `ledger` service, not in `agent` or `chain`
- Any payment, x402 call, chain transfer, escrow lock, release, or refund MUST call route_payment_intent first
- After routing, use only the returned allowedTools; if the router returns needs_clarification, ask the user before paying
- Agent-facing ledger access is through dynamically loaded `ledger` MCP tools: route_payment_intent, agent_wallet_get_ledger_state, agent_wallet_credit_balance, agent_wallet_create_escrow, agent_wallet_release_escrow, agent_wallet_refund_escrow
- Matched A2A task settlement should use `ledger` escrow flows; x402 is for immediate paid HTTP/API calls
- Never call x402 or fund Freqtrade dry-run without checking wealth status first
- Circle test wallets are not practically deletable; for Agent Wallet testing, always check for and reuse an existing test wallet before creating a new one
- Autonomous loop disabled by default (AUTONOMY_ENABLED=false) - prevents accidental spending
- Default strategy: SimpleAgentStrategy (EMA 9/21 crossover on 5m ETH/USDC)

## Key Environment Variables
- Agent: OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_ENDPOINT, BRAIN_AGENT_MODEL
- Chain: PRIVATE_KEY, RPC_URL, CHAIN_ID, CHAIN_MOCK
- Ledger: LEDGER_STATE_PATH
- Freqtrade: FREQTRADE_USERNAME, FREQTRADE_PASSWORD, FREQTRADE_ALLOW_WRITE_ACTIONS

## Testing & Verification
- Health check: `curl http://localhost:8000/health`
- Ledger health check: `curl http://localhost:8092/health`
- Ledger MCP endpoint: `http://localhost:8092/mcp/`
- Ledger state: `curl http://localhost:8092/ledger/state`
- Agent session: POST /agent/sessions then POST /agent/sessions/{id}/messages
- Wealth status: GET /agent/run with input "check wealth status"
