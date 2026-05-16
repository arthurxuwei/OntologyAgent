# OntologyAgent

当前仓库由三条能力线组成：

- `agent`：Python Agent 本体，负责交互式会话、tool orchestration，以及对子 Agent 的管理
- `chain`：TypeScript 链上 MCP skill provider，负责直接链上执行、UserOperation、交易状态和 x402 buyer flow
- `circle`：独立 Circle MCP skill provider，负责 Agent Wallet 生命周期、Circle 钱包状态和 ledger release 的真实结算
- `ledger`：独立链下账本服务，负责 Agent Wallet 内部余额、Escrow 锁款、放款、退款和流水记录

Agent / ZeroClaw 安装包已拆到独立仓库：[chief-install](https://github.com/arthurxuwei/chief-install)。

另外还有一个仅用于本地测试的辅助服务：

- `x402-seller`：独立的 x402 seller demo 资源服务
- `x402-mock`：独立的 mock facilitator，用于 `CHAIN_MOCK=true` 的本地 x402 回归

## 同时启动

在仓库根目录执行：

```bash
docker compose up -d --build
```

启动后默认可用的入口：

- `agent` 健康检查：`http://localhost:8000/health`
- `agent` Web 控制台：`http://localhost:8000/`
- `agent` 单轮调用：`POST /agent/run`
- `agent` 交互式会话：`POST /agent/sessions` 和 `POST /agent/sessions/{sessionId}/messages`
- `agent` 子 Agent 管理：`POST /autonomy/start`、`POST /autonomy/stop`、`POST /autonomy/tick`
- `ledger` 管理页面：`http://localhost:8092/`
- `ledger` 健康检查：`http://localhost:8092/health`
- `ledger` 账本状态：`GET http://localhost:8092/ledger/state`
- `x402-seller` 健康检查：`http://localhost:8001/health`
- `x402-seller` 演示资源：`GET http://localhost:8001/x402/demo-resource`
- `x402-seller` Circle Nanopayments 演示资源：`GET http://localhost:8001/x402/agent-services/research-summary/nanopayments`
- `chain` MCP：`http://localhost:8091/mcp/`
- `circle` MCP：`http://localhost:8093/mcp/`

## Worktree 注意事项

如果你是在 `.worktrees/...` 目录里执行 `docker compose`，Compose 默认会读取**当前目录**下的 `.env`，不会自动回退到主仓库根目录的 `.env`。

这会导致一些关键变量在容器里变成空值，例如：

- `PRIVATE_KEY`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`

进一步可能出现：

- `chain` signer 未配置
- `agent` 模型配置缺失
- 主工作区和 worktree 启动行为不一致

推荐在 worktree 中显式指定主仓库根目录的 `.env`：

```bash
docker compose --env-file "$(dirname "$(git rev-parse --git-common-dir)")/.env" up -d
```

这样可以确保 Compose 使用当前仓库根目录的环境变量，而不是 worktree 目录下不存在或不完整的 `.env`。

## MCP 架构

当前设计里：

- `agent` 是 agent 本体，不承载 seller resource
- `ledger` 是独立链下账本服务，不属于 `agent` 或 `chain` 进程
- 链上动作只能通过 `chain` MCP tools 完成
- Circle Agent Wallet 生命周期和 Circle 转账结算只能通过 `circle` MCP tools 完成
- `chain` 不再提供 Fastify HTTP 业务接口
- 撮合型 A2A 的 Escrow/余额状态应落在 `ledger`，而不是用 x402 或链上交易直接承担

`agent` 启动后会同时发现三类内部工具：

- 本地 wealth 工具
  - `get_wealth_status`
  - `start_wealth_agent`
  - `stop_wealth_agent`
  - `run_wealth_tick`
  - `update_wealth_config`
- 本地 Agent Wallet ledger 工具
  - `route_payment_intent`（任何付款、x402、转账或 Escrow 动作前先选择支付方式）
  - `agent_wallet_get_ledger_state`
  - `agent_wallet_credit_balance`
  - `agent_wallet_create_onramp_session`
  - `agent_wallet_create_escrow`
  - `agent_wallet_release_escrow`
  - `agent_wallet_refund_escrow`
- `chain` MCP tools
  - `chain_get_wallet_state`（内部账本 / 自治循环使用）
  - `chain_sign_transfer`
  - `chain_submit_execution`
  - `chain_submit_user_operation`
  - `chain_x402_fetch`
- `circle` MCP tools
  - `agent_wallet_get_or_create`
  - `agent_wallet_status`

### Offchain Ledger Service（V1）

`ledger` 是独立 FastAPI 服务，用本地 JSON 文件保存第一版 Agent Wallet 链下账本。它面向撮合型 A2A 场景，提供内部余额和 Escrow 状态机，不负责链上签名、x402 付款或 owner 鉴权。

主要接口：

- `GET /health`
- `GET /ledger/state`
- `POST /ledger/accounts/{agentId}/credit`
- `POST /ledger/escrows`
- `POST /ledger/escrows/{escrowId}/release`
- `POST /ledger/escrows/{escrowId}/refund`
- `POST /onramp/sessions`
- `GET /onramp/sessions/{sessionId}`
- `POST /onramp/sessions/{sessionId}/confirm`

账本规则：

- 金额使用 USDC atomic amount 字符串，不使用浮点数
- `credit` 增加 Agent 可用余额
- Coinbase Onramp 创建 session 时只生成 hosted onramp URL，不增加 Agent 可用余额
- Onramp 确认后才会创建 `coinbase_onramp_confirmed` credit entry
- 同一个 Onramp session 重复 confirm 不会重复入账
- 创建 escrow 会把 buyer 的可用余额转为锁定余额
- `release` 会把 buyer 锁定余额转给 seller 可用余额
- `refund` 会把 buyer 锁定余额退回 buyer 可用余额
- 已 release/refund 的 escrow 不能再次变更

本地状态文件由 `LEDGER_STATE_PATH` 控制，Docker 默认是 `/app/data/offchain_ledger.json`，并挂载到仓库的 `./ledger/data`。

`ledger` 自带独立管理页面：`http://localhost:8092/`。页面可以直接验证 Coinbase Onramp session、onramp confirm credit、manual credit、create escrow、release 和 refund。`agent` 的 Web Console 不承载 ledger 管理功能；agent 只通过本地工具在对话/编排时调用 ledger 能力。

### Payment Routing

Agent 同时具备 `ledger`、x402 和链上转账能力。为了避免模型自行猜测支付方式，所有付款相关动作必须先调用 `route_payment_intent`：

- `deliveryMode=async_task` 或 `requiresAcceptance=true` 且不是外部服务：返回 `ledger_escrow`
- `deliveryMode=funding`：返回 `onramp`
- `deliveryMode=immediate_api` 且是外部服务：返回 `x402`
- `deliveryMode=withdrawal`：返回 `chain_transfer`
- 信息不足或外部异步交付：返回 `needs_clarification`

Agent 只能继续使用路由结果里的 `allowedTools`。如果返回 `needs_clarification`，应先向用户澄清交易类型、交付方式和对方是否为外部服务。

### Coinbase Onramp Alpha

`ledger` 现在承载 Alpha 版 Coinbase hosted onramp session 和确认入账流程。当前实现使用 Coinbase Onramp Session Token API 生成 `sessionToken`，再返回 `https://pay.coinbase.com/buy/select-asset?...` hosted URL。用户完成 Coinbase onramp 前，ledger 不会增加可用余额；只有确认接口收到实际到账 atomic amount 后，才会写入 `credit` entry。

本地 mock：

```bash
COINBASE_ONRAMP_MOCK=true docker compose up -d --build ledger
```

创建 onramp session：

```bash
curl -X POST http://localhost:8092/onramp/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "agentId": "agentA",
    "destinationAddress": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
    "paymentAmount": "10.00",
    "idempotencyKey": "fund-agentA-10"
  }'
```

返回的 `onrampUrl` 应交给前端打开。`idempotencyKey` 重复时会复用已有 session。

确认入账：

```bash
curl -X POST http://localhost:8092/onramp/sessions/{sessionId}/confirm \
  -H "Content-Type: application/json" \
  -d '{
    "providerOrderId": "coinbase_order_123",
    "amountAtomic": "10000000",
    "txHash": "0xabc123"
  }'
```

`amountAtomic` 必须是实际确认到账的 USDC atomic amount。重复 confirm 同一个 session 会返回已 credited 的 session，不会再次 credit ledger。

真实 Coinbase 配置：

```bash
COINBASE_ONRAMP_MOCK=false
COINBASE_API_KEY_ID="<id from the Coinbase API key JSON>"
COINBASE_API_PRIVATE_KEY="<base64 privateKey from the Coinbase API key JSON>"
```

如果你已经有可直接用于 Onramp API 的 bearer token，也可以设置：

```bash
COINBASE_ONRAMP_BEARER_TOKEN=...
```

默认 Coinbase 参数：

- `COINBASE_ONRAMP_API_BASE_URL=https://api.developer.coinbase.com`
- `COINBASE_ONRAMP_TOKEN_PATH=/onramp/v1/token`
- `COINBASE_ONRAMP_HOSTED_URL=https://pay.coinbase.com/buy/select-asset`

## 自治运行

当前 `agent` 同时支持三条运行路径：

- `POST /agent/run`：单轮调用
- `POST /agent/sessions` + `POST /agent/sessions/{sessionId}/messages`：持续交互式会话
- 后台自治循环：按固定周期读取链上钱包和 ledger 状态，再由一个理财子 Agent 做保护性判断

自治循环默认是 **关闭** 的，避免一启动就自动花费真实资产。启用后：

- 启动资金默认来自链上钱包余额
- 自治子 Agent 只关心钱包余额、ledger 状态和风险阈值
- 自治子 Agent 不会主动调用 x402
- 后续统一按资金健康账本跟踪：
  - `startingCapitalEth`
  - `startingCapitalUsd`
  - `currentWalletBalanceEth`
  - `currentWalletBalanceUsd`
  - `netWorthEstimate`
  - `healthStatus`
  - `lastProtectiveAction`
  - `lastFundingRecommendation`

可通过：

- `GET /health`
- `GET /autonomy/status`
- `POST /autonomy/start`
- `POST /autonomy/stop`
- `POST /autonomy/tick`
- `POST /autonomy/config`

查看当前自治状态和账本快照，也可以显式管理子 Agent 的生命周期。

管家仍然负责：

- 是否调用 x402
- 是否执行其他链上动作

在这些业务动作之前，建议先调用 `get_wealth_status` 查看理财子 Agent 的当前状态。管家也可以直接通过 `start_wealth_agent`、`stop_wealth_agent`、`run_wealth_tick` 管理子 Agent，并通过 `update_wealth_config` 调整运行时风控阈值。

## 演示脚本

### Agent 交互入口

浏览器入口：

```text
http://localhost:8000/
```

这个页面会：

- 创建和复用 `agent` session
- 直接和管家多轮对话
- 在侧边栏查看、启动、停止、手动执行子 Agent
- 展示最近一次守门建议和状态摘要

```bash
./scripts/agent-chat.sh
```

这个脚本会：

- 创建一个新的 `agent` 会话
- 持续读取你的输入并发送到同一个 session
- 允许你在会话里直接和管家多轮交互
- 支持几个内建命令：
  - `/wealth-status`
  - `/wealth-start`
  - `/wealth-stop`
  - `/wealth-tick`

如需自定义地址：

```bash
AGENT_BASE_URL=http://localhost:8000 ./scripts/agent-chat.sh
```

### x402 Nanopayments 集成验证

可以跑 buyer/seller/facilitator 集成用例。先让 compose 里的 `x402-seller`
使用 mock facilitator，然后在 `chain` 容器里执行：

```bash
X402_PAY_TO=0x2222222222222222222222222222222222222222 \
X402_FACILITATOR_URL=http://x402-mock:8000/x402/facilitator \
docker compose up -d --build x402-mock x402-seller

docker run --rm --network ontologyagent_default \
  -v "$PWD/chain:/workspace" \
  -w /workspace \
  -e RUN_X402_NANOPAYMENTS_INTEGRATION=true \
  ontologyagent-chain \
  node --import tsx --test test/x402-nanopayments.integration.test.ts
```

如需验证真实 Circle Gateway facilitator / live settlement，先准备：

- `X402_PAY_TO` / `X402_LIVE_PAY_TO`：seller 收款地址，两者应一致，且不能等于 buyer 地址
- `X402_LIVE_BUYER_PRIVATE_KEY`：buyer 测试私钥，必须有足够 Base Sepolia ETH、Base Sepolia USDC 和 Circle Gateway available balance
- `RPC_URL`：Base Sepolia RPC，默认 `https://base-sepolia-rpc.publicnode.com`
- `X402_LIVE_FACILITATOR_URL`：Circle Gateway testnet facilitator，默认 `https://gateway-api-testnet.circle.com`

注意：Circle Gateway nanopayments 使用 Gateway balance，不是普通钱包里的 USDC
余额。首次 live 测试前，需要先通过 Circle Gateway deposit 少量测试 USDC。

然后运行：

```bash
X402_PAY_TO=0x... \
X402_FACILITATOR_URL="${X402_LIVE_FACILITATOR_URL:-https://gateway-api-testnet.circle.com}" \
docker compose up -d --build x402-seller

docker run --rm --network ontologyagent_default \
  -v "$PWD/chain:/workspace" \
  -w /workspace \
  -e RUN_X402_NANOPAYMENTS_LIVE=true \
  -e X402_LIVE_BUYER_PRIVATE_KEY=0x... \
  -e X402_LIVE_PAY_TO=0x... \
  -e X402_LIVE_FACILITATOR_URL="${X402_LIVE_FACILITATOR_URL:-https://gateway-api-testnet.circle.com}" \
  -e RPC_URL="${RPC_URL:-https://base-sepolia-rpc.publicnode.com}" \
  ontologyagent-chain \
  node --import tsx --test test/x402-nanopayments.integration.test.ts
```

这个 live 用例会真实执行 paid x402 fetch，强制选择 `GatewayWalletBatched`。
若失败，通常可以按阶段判断：

- seller 没返回 `402`：检查 `X402_PAY_TO` 是否配置
- 找不到 `GatewayWalletBatched`：检查 seller nanopayments endpoint / Gateway contract 配置
- policy 拒绝：检查 `X402_LIVE_PAY_TO`、USDC cap 或 whitelist
- `self_transfer`：检查 seller `payTo` 是否等于 buyer 地址
- `authorization_validity_too_short`：检查 seller 是否广告至少 7 天以上的 Gateway `maxTimeoutSeconds`
- verify / settle 失败：检查 buyer 签名格式、Gateway balance、facilitator 是否支持 Circle Gateway

### Simplescraper live x402 验证

```bash
PRIVATE_KEY=0x... \
./scripts/live-x402-simplescraper.sh
```

脚本会通过 `chain` MCP tool `chain_x402_fetch` 请求：

- `POST https://api.simplescraper.io/v1/extract`

默认抓取目标：

- `https://example.com`

## 默认链与协议

- 默认 RPC：`https://base-sepolia-rpc.publicnode.com`
- 默认链 ID：`84532`
- 默认 x402 网络：`eip155:84532`
- 默认 facilitator：`https://x402.org/facilitator`
- 默认 x402 资产：Base Sepolia USDC
- seller / buyer 使用标准头：
  - `PAYMENT-REQUIRED`
  - `PAYMENT-SIGNATURE`
  - `PAYMENT-RESPONSE`

## 关键环境变量

### agent

- `OPENAI_API_KEY`：会被 `docker compose` 直接注入 `agent` 容器；建议放在仓库根目录未提交的 `.env`
- `OPENAI_BASE_URL`：OpenAI 兼容 endpoint；会被 `docker compose` 直接注入 `agent` 容器
- `OPENAI_ENDPOINT`：`OPENAI_BASE_URL` 的兼容别名；如两者都设置，优先使用 `OPENAI_BASE_URL`
- `BRAIN_AGENT_MODEL`：管家使用的模型名；会被 `docker compose` 直接注入 `agent` 容器，默认 `gpt-4o-mini`
- `CHAIN_MCP_URL`：`agent` 访问链上 MCP 的地址，默认 `http://chain-mcp:8091/mcp/`
- `CHAIN_TIMEOUT_SECONDS`：请求相关超时，默认 `20`
- `BRAIN_AGENT_MODEL`：Agent 模型名，默认 `gpt-4o-mini`
- `AUTONOMY_ENABLED`：是否开启后台自治循环，默认 `false`
- `AUTONOMY_INTERVAL_SECONDS`：自治轮询间隔，默认 `60`
- `AUTONOMY_STATE_PATH`：自治账本状态文件，默认 `/app/data/autonomy_state.json`
- `AUTONOMY_ETH_PRICE_USD`：用于把链上 ETH 预算折算为统一 USD 视图的参考价格，默认 `3000`
- `AUTONOMY_MIN_WALLET_BALANCE_USD`：低于该值时建议补充资金，默认 `250`
- `AUTONOMY_STOP_TRADING_BALANCE_USD`：低于该值时自治子 Agent 可停止交易，默认 `150`
- `AUTONOMY_FORCE_EXIT_BALANCE_USD`：低于该值时自治子 Agent 可强制平仓，默认 `75`
- `AUTONOMY_MAX_DRAWDOWN_RATIO`：最大回撤阈值比例，默认 `0.15`
- `AUTONOMY_MODEL`：自治循环专用模型；为空时回退到 `BRAIN_AGENT_MODEL`
- `GITHUB_CLIENT_ID`：Agent Wallet MVP 的 GitHub OAuth client id
- `GITHUB_CLIENT_SECRET`：Agent Wallet MVP 的 GitHub OAuth client secret
- `AUTH_SESSION_SECRET`：签名 Agent Wallet owner session cookie；Docker 默认使用本地开发 secret
- `PUBLIC_BASE_URL`：OAuth callback 使用的公开 base URL，默认 `http://localhost:8000`
- `AGENT_WALLET_STATE_PATH`：Agent Wallet 本地 demo 状态文件，Docker 默认 `/app/data/agent_wallet_state.json`
- `LEDGER_URL`：`agent` 访问独立链下账本服务的内部地址，默认 `http://ledger:8092`
- `LEDGER_TIMEOUT_SECONDS`：`agent` 请求链下账本服务的超时时间，默认 `20`
- `X402_SELLER_BASE_URL`：Agent Wallet UI 调用 seller 服务时使用的内部 base URL，默认 `http://x402-seller:8000`


`update_wealth_config` 和 `POST /autonomy/config` 可以在运行时修改以下自治配置，并会把覆盖值写入 `AUTONOMY_STATE_PATH`，重启后继续生效：

- `intervalSeconds`
- `ethPriceUsd`
- `minWalletBalanceUsd`
- `stopTradingBalanceUsd`
- `forceExitBalanceUsd`
- `maxDrawdownRatio`
### chain

- `CHAIN_MCP_PORT`：chain MCP 端口，默认 `8091`
- `PRIVATE_KEY`：链上执行和 x402 buyer 默认签名私钥
- `RPC_URL`：链 RPC 地址，默认 Base Sepolia
- `CHAIN_ID`：期望连接的链 ID，默认 `84532`
- `DAILY_LIMIT`：每日可执行总额度，默认 `2.0`
- `SINGLE_TX_CAP`：单笔 ETH 额度上限，默认 `1.0`
- `WHITELISTED_RECIPIENTS`：额外白名单地址，逗号分隔
- `CHAIN_MOCK`：是否使用模拟链执行，默认 `false`
- `CHAIN_MOCK_BALANCE_ETH`：mock 模式下链上钱包返回给自治账本的余额，默认 `1.0`
- `CHAIN_MOCK_USDC_BALANCE`：mock 模式下 `chain_get_wallet_state` 返回的 USDC 余额，默认 `0`
- `TRADE_INTENT_PAIR`：trade intent 默认交易对，默认 `ETH/USDC`
- `TRADE_INTENT_SELL_TOKEN`：trade intent 默认卖出 Token，默认 Base Sepolia USDC
- `TRADE_INTENT_BUY_TOKEN`：trade intent 默认买入 Token，默认 Base Sepolia WETH
- `BUNDLER_RPC_URL`：ERC-4337 Bundler RPC
- `ENTRY_POINT_ADDRESS`：ERC-4337 EntryPoint
- `X402_FACILITATOR_URL`：x402 facilitator 地址
- `X402_NETWORK`：x402 CAIP-2 网络标识
- `X402_BUYER_PRIVATE_KEY`：x402 buyer 专用私钥；为空时回退到 `PRIVATE_KEY`
- `X402_USDC_SINGLE_CAP`：x402 单笔 USDC 上限，默认 `1.0`
- `X402_USDC_DAILY_CAP`：x402 每日 USDC 上限，默认 `2.0`

### circle

- `CIRCLE_MCP_PORT`：circle MCP 端口，默认 `8093`
- `AGENT_WALLET_STATE_PATH`：Agent Wallet 本地 demo 状态文件，Docker 默认 `/app/data/agent_wallet_state.json`
- `CIRCLE_API_KEY`：Circle sandbox API key；`CHAIN_MOCK=false` 且创建真实 Agent Wallet 时需要
- `CIRCLE_ENTITY_SECRET`：Circle entity secret；用于按请求生成 entity secret ciphertext
- `CIRCLE_ENTITY_SECRET_CIPHERTEXT`：兼容旧配置，仅用于本地 mock/迁移场景；真实 Circle 请求会要求 `CIRCLE_ENTITY_SECRET`
- `CIRCLE_WALLET_SET_ID`：已有 Circle wallet set id；为空时由 Circle wallet service 创建/使用默认流程
- `CIRCLE_BASE_URL`：Circle Web3 Services base URL，默认 `https://api.circle.com/v1/w3s`
- `CIRCLE_USDC_TOKEN_ID`：Circle USDC token id；真实 Circle transfer 时使用

### ledger

- `LEDGER_STATE_PATH`：链下账本 JSON 状态文件路径；Docker 默认 `/app/data/offchain_ledger.json`
- `COINBASE_ONRAMP_MOCK`：是否使用本地 mock Coinbase onramp token，默认 `false`
- `COINBASE_ONRAMP_BEARER_TOKEN`：可选；如果配置，会直接作为 Coinbase Onramp API bearer token 使用
- `COINBASE_API_KEY_ID`：Coinbase CDP Secret API Key JSON 中的 `id`
- `COINBASE_API_PRIVATE_KEY`：Coinbase CDP Secret API Key JSON 中的 base64 `privateKey`
- `COINBASE_ONRAMP_API_BASE_URL`：Coinbase API base URL，默认 `https://api.developer.coinbase.com`
- `COINBASE_ONRAMP_TOKEN_PATH`：Coinbase Onramp session token path，默认 `/onramp/v1/token`
- `COINBASE_ONRAMP_HOSTED_URL`：Coinbase hosted onramp URL，默认 `https://pay.coinbase.com/buy/select-asset`
- `LEDGER_CHAIN_MCP_URL`：ledger 链上审计记录使用的 chain MCP 地址，默认 `http://chain-mcp:8091/mcp/`
- `LEDGER_SETTLEMENT_MCP_URL`：ledger release 真实结算使用的 Circle MCP 地址，默认 `http://circle-mcp:8093/mcp/`

## Agent Wallet MVP x402 Demo

Agent Wallet MVP 在现有 Web Console 中增加了一个 `Agent Wallet MVP` 面板，用来跑通第一版 A2A 付费服务流程：

1. 使用 GitHub OAuth 登录 owner session
2. 创建 Circle sandbox Agent Wallet
3. 用一次性 claim code 认领该钱包
4. 注册 `/x402/agent-services/research-summary`
5. 在 Base Sepolia 上触发一次 x402 paid service call
6. 在 Agent Wallet demo 状态中查看 service 与 x402 payment 记录

注意：Agent Wallet 的 x402 demo payment 记录仍存储在 `agent` 的 demo state 中；撮合型 A2A 的内部余额和 Escrow 状态由独立 `ledger` 服务负责。

最小配置：

- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `AUTH_SESSION_SECRET`
- `PUBLIC_BASE_URL`
- `CIRCLE_API_KEY`
- `CIRCLE_ENTITY_SECRET`
- `CIRCLE_WALLET_SET_ID`
- `X402_BUYER_PRIVATE_KEY`
- `X402_NETWORK=eip155:84532`
- `X402_USDC_ASSET_ADDRESS=0x036CbD53842c5426634e7929541eC2318f3dCF7e`

本地 demo 状态存储在 `AGENT_WALLET_STATE_PATH`，Docker 默认路径是 `/app/data/agent_wallet_state.json`。需要清空本地 Agent Wallet demo 状态时，可以调用：

```bash
curl -X POST http://localhost:8000/agent-wallet/reset
```

独立链下账本状态存储在 `LEDGER_STATE_PATH`。本地清空账本可删除 `ledger/data/offchain_ledger.json` 后重启 `ledger` 服务。

### x402-seller

- `X402_PAY_TO`：seller 收款地址
- `X402_PRICE`：seller 演示资源价格，默认 `$0.01`
- `X402_NETWORK`：seller CAIP-2 网络，默认 `eip155:84532`
- `X402_FACILITATOR_URL`：seller 使用的 facilitator，默认 `https://x402.org/facilitator`
- `X402_TIMEOUT_SECONDS`：seller 请求 facilitator 的超时，默认 `20`
- `X402_GATEWAY_VERIFYING_CONTRACT`：Circle Gateway `GatewayWalletBatched` verifying contract；Base Sepolia 默认 `0x0077777d7EBA4688BDeF3E311b846F25870A19B9`

## 交互式 Agent

`agent` 内置 LangGraph Agent，会自动使用：

- `chain` 链上 MCP tools
- 本地的理财子管理工具

单轮调用示例：

```json
{
  "input": "先访问 x402 demo 资源，再决定是否提交链上执行"
}
```

多轮会话流程：

1. `POST /agent/sessions` 创建 session
2. `POST /agent/sessions/{sessionId}/messages` 持续发送消息
3. `GET /agent/sessions/{sessionId}` 查看当前 session 状态

## `x402-seller: GET /x402/demo-resource`

这是独立 `x402-seller` 服务提供的标准 x402 seller 演示资源：

- 首次访问会返回 `402 Payment Required`
- 响应头包含 `PAYMENT-REQUIRED`
- buyer 成功重试后，返回业务 JSON，并带 `PAYMENT-RESPONSE`

## `x402-seller: GET /x402/agent-services/research-summary/nanopayments`

这是独立 `x402-seller` 服务提供的 Circle Nanopayments 风格演示资源：

- 首次访问会返回 `402 Payment Required`
- `accepts` 同时包含标准 x402 `exact` 付款项和 Circle Gateway `GatewayWalletBatched` 付款项
- seller 会按 buyer 在 `PAYMENT-SIGNATURE.accepted` 中选择的付款项执行 verify / settle，而不是固定使用第一项
- buyer 可在 `chain_x402_fetch` 中传 `paymentPreference: "circle-gateway"`，优先选择 `GatewayWalletBatched`
- buyer 成功重试后，返回 `research-summary-nanopayments` 业务 JSON，并带 `PAYMENT-RESPONSE`

在 `CHAIN_MOCK=true` 的本地回归场景下，脚本会把 facilitator 切到独立的 `x402-mock` 服务，而不是 `agent` 本体。
