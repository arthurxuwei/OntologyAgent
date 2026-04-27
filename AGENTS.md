# OntologyAgent Agent Guidelines

## Essential Commands
- Start all services: `docker compose up -d --build`
- Worktree docker compose: `docker compose --env-file "$(dirname "$(git rev-parse --git-common-dir)")/.env" up -d`
- Agent chat: `./scripts/agent-chat.sh`
- Chain MCP demo: `./scripts/demo-chain-mcp.sh`
- Freqtrade MCP demo: `./scripts/demo-freqtrade-mcp.sh`
- Live x402 test: `PRIVATE_KEY=0x... ./scripts/live-x402-simplescraper.sh`

## Critical Architecture Notes
- Chain actions ONLY via chain MCP tools (signing, execution, UserOperations, x402 fetch)
- Trading/quant actions ONLY via Freqtrade MCP tools (start/stop bot, evaluate signal, force trades)
- Never call x402 or fund Freqtrade dry-run without checking wealth status first
- Circle test wallets are not practically deletable; for Agent Wallet testing, always check for and reuse an existing test wallet before creating a new one
- Autonomous loop disabled by default (AUTONOMY_ENABLED=false) - prevents accidental spending
- Default strategy: SimpleAgentStrategy (EMA 9/21 crossover on 5m ETH/USDC)

## Key Environment Variables
- Agent: OPENAI_API_KEY, BRAIN_AGENT_MODEL, AUTONOMY_ENABLED
- Chain: PRIVATE_KEY, RPC_URL, CHAIN_ID, CHAIN_MOCK
- Freqtrade: FREQTRADE_USERNAME, FREQTRADE_PASSWORD, FREQTRADE_ALLOW_WRITE_ACTIONS

## Testing & Verification
- Health check: `curl http://localhost:8000/health`
- Agent session: POST /agent/sessions then POST /agent/sessions/{id}/messages
- Wealth status: GET /agent/run with input "check wealth status"
