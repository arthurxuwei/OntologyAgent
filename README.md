# OntologyAgent

当前仓库由三条能力线组成：

- `agent`：Python Agent 本体，负责 tool orchestration、x402 seller 资源和 `/agent/run`
- `chain`：TypeScript 链上 MCP skill provider，负责钱包、执行、UserOperation 和 x402 buyer flow
- `freqtrade`：单容器 Freqtrade + MCP skill provider，负责量化策略和 CEX 交易技能

## 同时启动

在仓库根目录执行：

```bash
docker compose up -d --build
```

启动后默认可用的入口：

- `agent` 健康检查：`http://localhost:8000/health`
- `agent` Agent 接口：`POST /agent/run`
- `agent` x402 seller 演示资源：`GET /x402/demo-resource`
- `chain` MCP：`http://localhost:8091/mcp/`
- `freqtrade` REST API：`http://localhost:8080/api/v1`
- `freqtrade` MCP：`http://localhost:8090/mcp/`

## MCP 架构

当前设计里：

- `agent` 是 agent 本体
- 链上动作只能通过 `chain` MCP tools 完成
- 中心化交易和量化动作只能通过 `freqtrade` MCP tools 完成
- `chain` 不再提供 Fastify HTTP 业务接口

`agent` 启动后会同时发现两组内部工具：

- `chain` MCP tools
  - `chain_sign_transfer`
  - `chain_submit_execution`
  - `chain_submit_user_operation`
  - `chain_x402_fetch`
- `freqtrade` MCP tools
  - `get_trading_status`
  - `list_strategies`
  - `get_open_trades`
  - `get_closed_trades`
  - `get_performance_summary`
  - `start_bot`
  - `stop_bot`
  - `pause_trading`
  - `resume_trading`
  - `force_enter_trade`
  - `force_exit_trade`

## 演示脚本

### Chain MCP 演示

```bash
./scripts/demo-chain-mcp.sh
```

这个脚本会：

- 启动完整 compose 栈
- 等待 `agent` 和 `chain` MCP 就绪
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

- `CHAIN_MCP_URL`：`agent` 访问链上 MCP 的地址，默认 `http://chain-mcp:8091/mcp/`
- `CHAIN_TIMEOUT_SECONDS`：请求相关超时，默认 `20`
- `FREQTRADE_MCP_URL`：`agent` 访问 Freqtrade MCP 的地址，默认 `http://freqtrade:8090/mcp/`
- `OPENAI_API_KEY`：Agent 模型密钥
- `BRAIN_AGENT_MODEL`：Agent 模型名，默认 `gpt-4o-mini`
- `X402_PAY_TO`：`agent` seller 收款地址
- `X402_PRICE`：seller 演示资源价格，默认 `$0.01`
- `X402_NETWORK`：seller CAIP-2 网络，默认 `eip155:84532`
- `X402_FACILITATOR_URL`：seller 使用的 facilitator，默认 `https://x402.org/facilitator`

### chain

- `CHAIN_MCP_PORT`：chain MCP 端口，默认 `8091`
- `PRIVATE_KEY`：链上执行和 x402 buyer 默认签名私钥
- `RPC_URL`：链 RPC 地址，默认 Base Sepolia
- `CHAIN_ID`：期望连接的链 ID，默认 `84532`
- `DAILY_LIMIT`：每日可执行总额度，默认 `2.0`
- `SINGLE_TX_CAP`：单笔 ETH 额度上限，默认 `1.0`
- `WHITELISTED_RECIPIENTS`：额外白名单地址，逗号分隔
- `CHAIN_MOCK`：是否使用模拟链执行，默认 `false`
- `BUNDLER_RPC_URL`：ERC-4337 Bundler RPC
- `ENTRY_POINT_ADDRESS`：ERC-4337 EntryPoint
- `X402_FACILITATOR_URL`：x402 facilitator 地址
- `X402_NETWORK`：x402 CAIP-2 网络标识
- `X402_BUYER_PRIVATE_KEY`：x402 buyer 专用私钥；为空时回退到 `PRIVATE_KEY`
- `X402_USDC_SINGLE_CAP`：x402 单笔 USDC 上限，默认 `1.0`
- `X402_USDC_DAILY_CAP`：x402 每日 USDC 上限，默认 `2.0`

### freqtrade

- `FREQTRADE_USERNAME`：Freqtrade REST API 用户名，默认 `freqtrade`
- `FREQTRADE_PASSWORD`：Freqtrade REST API 密码，默认 `freqtrade`
- `FREQTRADE_TIMEOUT_SECONDS`：Freqtrade API / MCP 调用超时，默认 `20`
- `FREQTRADE_ALLOW_WRITE_ACTIONS`：是否允许写操作，默认 `true`
- `FREQTRADE_STRATEGY_NAME`：默认策略，默认 `SimpleAgentStrategy`

## `/agent/run`

`agent` 内置 LangGraph Agent，会自动使用：

- `chain` 链上 MCP tools
- `freqtrade` 投资 MCP tools

请求示例：

```json
{
  "input": "先访问 x402 demo 资源，再决定是否提交链上执行"
}
```

## `GET /x402/demo-resource`

这是 `agent` 内置的标准 x402 seller 演示资源：

- 首次访问会返回 `402 Payment Required`
- 响应头包含 `PAYMENT-REQUIRED`
- buyer 成功重试后，返回业务 JSON，并带 `PAYMENT-RESPONSE`

在 `CHAIN_MOCK=true` 的本地回归场景下，脚本会把 facilitator 切到 `agent` 内置 mock facilitator。
