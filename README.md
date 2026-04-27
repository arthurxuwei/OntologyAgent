# OntologyAgent

当前仓库由三条能力线组成：

- `agent`：Python Agent 本体，负责交互式会话、tool orchestration，以及对子 Agent 的管理
- `chain`：TypeScript 链上 MCP skill provider，负责钱包、执行、UserOperation 和 x402 buyer flow
- `freqtrade`：单容器 Freqtrade + MCP skill provider，负责量化策略和 CEX 交易技能
- `ledger`：独立链下账本服务，负责 Agent Wallet 内部余额、Escrow 锁款、放款、退款和流水记录

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
- `x402-seller` 演示资源：`GET /x402/demo-resource`
- `chain` MCP：`http://localhost:8091/mcp/`
- `freqtrade` REST API：`http://localhost:8080/api/v1`
- `freqtrade` MCP：`http://localhost:8090/mcp/`

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
- 中心化交易和量化动作只能通过 `freqtrade` MCP tools 完成
- `chain` 不再提供 Fastify HTTP 业务接口
- 撮合型 A2A 的 Escrow/余额状态应落在 `ledger`，而不是用 x402 或链上交易直接承担

`agent` 启动后会同时发现三类内部工具：

- 本地 wealth 工具
  - `get_wealth_status`
  - `start_wealth_agent`
  - `stop_wealth_agent`
  - `run_wealth_tick`
  - `update_wealth_config`
  - `execute_freqtrade_trade_intent`（让 `freqtrade` 生成 trade intent，再由 `chain` 用 Base 钱包执行）
- 本地 Agent Wallet ledger 工具
  - `agent_wallet_get_ledger_state`
  - `agent_wallet_credit_balance`
  - `agent_wallet_create_escrow`
  - `agent_wallet_release_escrow`
  - `agent_wallet_refund_escrow`
- `chain` MCP tools
  - `chain_get_wallet_state`（内部账本 / 自治循环使用）
  - `chain_sign_transfer`
  - `chain_submit_execution`
  - `chain_submit_user_operation`
  - `chain_x402_fetch`
  - `chain_execute_trade_intent`
- `freqtrade` MCP tools
  - `get_trading_status`
  - `list_strategies`
  - `get_open_trades`
  - `get_closed_trades`
  - `get_performance_summary`
  - `evaluate_trade_signal`
  - `start_bot`
  - `stop_bot`
  - `pause_trading`
  - `resume_trading`
  - `force_enter_trade`
  - `force_exit_trade`
  - `get_budget_snapshot`（内部账本 / 自治循环使用）
  - `sync_dry_run_wallet`（内部 dry-run 资金同步使用）

### Freqtrade Signal Evaluation（V1）

- `evaluate_trade_signal` 是只读工具，不会创建 trade intent，也不会触发链上执行
- V1 仅支持 `ETH/USDC`
- V1 返回 `buy` / `sell` / `hold`，并附带 `reason`、`confidence` 和仓位上下文

### Offchain Ledger Service（V1）

`ledger` 是独立 FastAPI 服务，用本地 JSON 文件保存第一版 Agent Wallet 链下账本。它面向撮合型 A2A 场景，提供内部余额和 Escrow 状态机，不负责链上签名、x402 付款或 owner 鉴权。

主要接口：

- `GET /health`
- `GET /ledger/state`
- `POST /ledger/accounts/{agentId}/credit`
- `POST /ledger/escrows`
- `POST /ledger/escrows/{escrowId}/release`
- `POST /ledger/escrows/{escrowId}/refund`

账本规则：

- 金额使用 USDC atomic amount 字符串，不使用浮点数
- `credit` 增加 Agent 可用余额
- 创建 escrow 会把 buyer 的可用余额转为锁定余额
- `release` 会把 buyer 锁定余额转给 seller 可用余额
- `refund` 会把 buyer 锁定余额退回 buyer 可用余额
- 已 release/refund 的 escrow 不能再次变更

本地状态文件由 `LEDGER_STATE_PATH` 控制，Docker 默认是 `/app/data/offchain_ledger.json`，并挂载到仓库的 `./ledger/data`。

`ledger` 自带独立管理页面：`http://localhost:8092/`。页面可以直接验证 credit、create escrow、release 和 refund。`agent` 的 Web Console 不承载 ledger 管理功能；agent 只通过本地工具在对话/编排时调用 ledger 能力。

### Default Freqtrade Strategy（V1）

- 默认策略 `SimpleAgentStrategy` 使用 `5m` 上的 `EMA 9/21` crossover
- 默认信号目标交易对是 `ETH/USDC`
- 默认情况下，`evaluate_trade_signal` 会基于该策略返回 `buy` / `sell` / `hold`

## 自治运行

当前 `agent` 同时支持三条运行路径：

- `POST /agent/run`：单轮调用
- `POST /agent/sessions` + `POST /agent/sessions/{sessionId}/messages`：持续交互式会话
- 后台自治循环：按固定周期读取链上钱包和 Freqtrade dry-run 状态，再由一个理财子 Agent 做保护性判断

自治循环默认是 **关闭** 的，避免一启动就自动花费真实资产。启用后：

- 启动资金默认来自链上钱包余额
- 自治子 Agent 只关心钱包余额、dry-run 盈亏和风险阈值
- 自治子 Agent 不会主动调用 x402，也不会自动同步 `dry_run_wallet`
- 后续统一按资金健康账本跟踪：
  - `startingCapitalEth`
  - `startingCapitalUsd`
  - `currentWalletBalanceEth`
  - `currentWalletBalanceUsd`
  - `dryRunRealizedPnl`
  - `dryRunUnrealizedPnl`
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
- 是否给 Freqtrade dry-run 增加资金
- 是否执行其他链上或交易动作

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

### Chain MCP 演示

```bash
./scripts/demo-chain-mcp.sh
```

这个脚本会：

- 启动完整 compose 栈
- 等待 `agent`、`chain` 和 `x402-seller` 就绪
- 发现 chain MCP tools
- 依次调用：
  - `chain_sign_transfer`
  - `chain_x402_fetch`
  - `chain_submit_execution`
  - `chain_submit_user_operation`

如需本地 mock 验证：

```bash
CHAIN_MOCK=true ./scripts/demo-chain-mcp.sh
```

如需 live 测试，至少准备：

```bash
PRIVATE_KEY=0x... \
DEMO_SIGN_TRANSFER_TO=0x... \
DEMO_X402_PAYMENT_TO=0x... \
X402_PAY_TO=0x... \
./scripts/demo-chain-mcp.sh
```

### Freqtrade MCP 演示

```bash
./scripts/demo-freqtrade-mcp.sh
```

这个脚本会：

- 启动 `agent`、`chain`、`freqtrade`
- 检查 `freqtrade` REST API 是否就绪
- 让 `agent` 通过 MCP 发现投资工具
- 调一次 `get_trading_status`

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
- `FREQTRADE_MCP_URL`：`agent` 访问 Freqtrade MCP 的地址，默认 `http://freqtrade:8090/mcp/`
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
- `CIRCLE_API_KEY`：Circle sandbox API key；`CHAIN_MOCK=false` 且创建真实 Agent Wallet 时需要
- `CIRCLE_ENTITY_SECRET`：Circle entity secret；用于按请求生成 entity secret ciphertext
- `CIRCLE_ENTITY_SECRET_CIPHERTEXT`：兼容旧配置，仅用于本地 mock/迁移场景；真实 Circle 请求会要求 `CIRCLE_ENTITY_SECRET`
- `CIRCLE_WALLET_SET_ID`：已有 Circle wallet set id；为空时由 Circle wallet service 创建/使用默认流程
- `CIRCLE_BASE_URL`：Circle Web3 Services base URL，默认 `https://api.circle.com/v1/w3s`

### ledger

- `LEDGER_STATE_PATH`：链下账本 JSON 状态文件路径；Docker 默认 `/app/data/offchain_ledger.json`

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

### Base 链上交易意图桥接（V1）

- Base 资金始终保留在 `chain` 钱包侧
- `freqtrade` 只负责生成 trade intent，不直接持有或执行链上资金
- `chain_execute_trade_intent` 在 `CHAIN_MOCK=true` 时返回 mock 结果，便于本地回归
- 非 mock 模式下，如果没有接入真实 DEX / aggregator，会返回结构化拒绝结果，而不是伪造成功

### x402-seller

- `X402_PAY_TO`：seller 收款地址
- `X402_PRICE`：seller 演示资源价格，默认 `$0.01`
- `X402_NETWORK`：seller CAIP-2 网络，默认 `eip155:84532`
- `X402_FACILITATOR_URL`：seller 使用的 facilitator，默认 `https://x402.org/facilitator`
- `X402_TIMEOUT_SECONDS`：seller 请求 facilitator 的超时，默认 `20`

### freqtrade

- `FREQTRADE_USERNAME`：Freqtrade REST API 用户名，默认 `freqtrade`
- `FREQTRADE_PASSWORD`：Freqtrade REST API 密码，默认 `freqtrade`
- `FREQTRADE_TIMEOUT_SECONDS`：Freqtrade API / MCP 调用超时，默认 `20`
- `FREQTRADE_ALLOW_WRITE_ACTIONS`：是否允许写操作，默认 `true`
- `FREQTRADE_STRATEGY_NAME`：默认策略，默认 `SimpleAgentStrategy`
- `FREQTRADE_CONFIG_PATH`：Freqtrade 配置路径；管家如需调整 `dry_run_wallet`，会通过 `sync_dry_run_wallet` 更新这里

## 交互式 Agent

`agent` 内置 LangGraph Agent，会自动使用：

- `chain` 链上 MCP tools
- `freqtrade` 投资 MCP tools
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

在 `CHAIN_MOCK=true` 的本地回归场景下，脚本会把 facilitator 切到独立的 `x402-mock` 服务，而不是 `agent` 本体。
