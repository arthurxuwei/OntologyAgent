# OntologyAgent

项目包含两个主要目录：

- `brain-py`：Python 业务逻辑（FastAPI），负责 Agent 和 x402 seller 资源
- `executor-ts`：TypeScript 链上执行器（Fastify），负责链执行和 x402 buyer flow

## 同时启动（Docker Compose）

在仓库根目录执行：

```bash
docker compose up --build
```

首次构建完成后，日常启动可直接用：

```bash
docker compose up -d
```

仅当 `brain-py/requirements.txt` 或 `executor-ts/package-lock.json` 变化时，再执行 `docker compose up --build -d`。

启动后：

- `brain-py` 健康检查：`http://localhost:8000/health`
- `brain-py` Agent 接口：`POST /agent/run`
- `brain-py` x402 seller 演示资源：`GET /x402/demo-resource`
- `executor-ts` 健康检查：`http://localhost:3000/health`
- `executor-ts` 接口：`POST /transfers/sign`、`POST /executions/submit`、`POST /user-operations/submit`、`POST /x402/fetch`

## 一键 curl 演示

在根目录执行：

```bash
./scripts/demo-curl.sh
```

脚本会自动：

- 启动 `docker compose`
- 等待 `brain-py` 与 `executor-ts` 就绪
- 调用 `POST /transfers/sign`
- 调用 `POST /x402/fetch`，演示标准 x402 `402 -> PAYMENT-SIGNATURE -> PAYMENT-RESPONSE`
- 调用 `POST /executions/submit`

默认直连 Base Sepolia，并将 x402 默认网络设置为 `eip155:84532`。

如需本地 mock 验证：

```bash
EXECUTOR_MOCK_CHAIN=true ./scripts/demo-curl.sh
```

如需未来做 live 验证，至少要准备：

```bash
PRIVATE_KEY=0x... \
DEMO_SIGN_TRANSFER_TO=0x... \
DEMO_X402_PAYMENT_TO=0x... \
X402_PAY_TO=0x... \
./scripts/demo-curl.sh
```

## Simplescraper live x402 验证

如果你要对真实外部 x402 服务做 live 验证，可以单独跑：

```bash
PRIVATE_KEY=0x... \
./scripts/live-x402-simplescraper.sh
```

脚本默认会请求：

- `POST https://api.simplescraper.io/v1/extract`
- 抓取目标 `https://example.com`

默认使用的 Simplescraper x402 收款地址是当前实测返回的：

- `0x6C01bea8570FDFDa471992d68e5C284A69A6B46d`

如需覆盖默认值，可传：

- `SIMPLESCRAPER_ENDPOINT`
- `SIMPLESCRAPER_TARGET_URL`
- `SIMPLESCRAPER_PAY_TO`

## 默认链与协议

- 默认 RPC：`https://base-sepolia-rpc.publicnode.com`
- 默认链 ID：`84532`
- 默认 x402 网络：`eip155:84532`
- 默认 facilitator：`https://x402.org/facilitator`
- 默认 x402 资产：Base Sepolia USDC
- seller/buyer 使用标准头：
  - `PAYMENT-REQUIRED`
  - `PAYMENT-SIGNATURE`
  - `PAYMENT-RESPONSE`

## 可选环境变量

- `EXECUTOR_PORT`：执行器端口（默认 `3000`）
- `PRIVATE_KEY`：执行器签名私钥；普通链执行和 x402 buyer 默认都可复用它
- `RPC_URL`：链 RPC 地址（默认 `https://base-sepolia-rpc.publicnode.com`）
- `CHAIN_ID`：期望连接的链 ID（默认 `84532`，即 Base Sepolia）
- `DAILY_LIMIT`：每日可签名总额度（ETH，默认 `2.0`）
- `SINGLE_TX_CAP`：单笔交易额度（ETH，默认 `1.0`，且受硬编码上限约束）
- `WHITELISTED_RECIPIENTS`：额外白名单地址，逗号分隔
- `EXECUTOR_MOCK_CHAIN`：是否使用模拟链执行（默认 `false`）
- `BUNDLER_RPC_URL`：ERC-4337 Bundler RPC（可选）
- `ENTRY_POINT_ADDRESS`：ERC-4337 EntryPoint（默认 `0x0576...1B57`）
- `EXECUTOR_BASE_URL`：`brain-py` 调用 `executor-ts` 的基地址（默认 `http://executor-ts:3000`）
- `EXECUTOR_TIMEOUT_SECONDS`：`brain-py` 调用 `executor-ts` 的超时时间秒数（默认 `20`）
- `OPENAI_API_KEY`：`brain-py` LangChain Agent 使用的模型密钥
- `BRAIN_AGENT_MODEL`：`brain-py` Agent 模型名（默认 `gpt-4o-mini`）
- `X402_FACILITATOR_URL`：x402 facilitator 地址（默认 `https://x402.org/facilitator`）
- `X402_NETWORK`：x402 CAIP-2 网络标识（默认 `eip155:84532`）
- `X402_PAY_TO`：`brain-py` seller 收款地址
- `X402_PRICE`：seller 演示资源价格（默认 `0.01`，也支持 `$0.01`）
- `X402_BUYER_PRIVATE_KEY`：x402 buyer 专用私钥；为空时回退到 `PRIVATE_KEY`
- `X402_USDC_SINGLE_CAP`：x402 单笔 USDC 上限（默认 `1.0`）
- `X402_USDC_DAILY_CAP`：x402 每日 USDC 上限（默认 `2.0`）
- `DEMO_SIGN_TRANSFER_TO`：演示脚本里 `/transfers/sign` 与 `/executions/submit` 使用的测试链地址
- `DEMO_X402_PAYMENT_TO`：演示脚本里 x402 收款地址默认值

## `POST /transfers/sign`

签名原生转账（不广播）：

```json
{
  "to": "0x000000000000000000000000000000000000dEaD",
  "amountEth": "0.01"
}
```

返回签名后的原始交易、tx hash，以及策略引擎快照。

## `POST /executions/submit`

提交一笔通用链上交易：

```json
{
  "to": "0x000000000000000000000000000000000000dEaD",
  "valueEth": "0",
  "data": "0x"
}
```

## `POST /user-operations/submit`

提交一笔 ERC-4337 UserOperation：

```json
{
  "target": "0x1111111111111111111111111111111111111111",
  "maxCostEth": "0.01",
  "raw": {
    "sender": "0x...",
    "nonce": "0x1",
    "initCode": "0x",
    "callData": "0x",
    "callGasLimit": "0x5208",
    "verificationGasLimit": "0x100000",
    "preVerificationGas": "0xc350",
    "maxFeePerGas": "0x59682f00",
    "maxPriorityFeePerGas": "0x59682f00",
    "paymasterAndData": "0x",
    "signature": "0x..."
  }
}
```

## `POST /x402/fetch`

通过官方 x402 buyer flow 访问付费资源：

```json
{
  "url": "http://brain-py:8000/x402/demo-resource",
  "method": "GET"
}
```

返回：

- 上游最终状态码和响应体
- 选中的 `accepts` requirement 摘要
- 解析后的 `PAYMENT-RESPONSE`
- x402 USDC 策略快照

## `GET /x402/demo-resource`

这是 `brain-py` 内置的标准 x402 seller 演示资源。

- 首次访问会返回 `402 Payment Required`
- 响应头里包含 `PAYMENT-REQUIRED`
- buyer 成功重试后，返回业务 JSON，并带 `PAYMENT-RESPONSE`

在 `EXECUTOR_MOCK_CHAIN=true` 的本地演示场景下，脚本会自动把 facilitator 切到 `brain-py` 内置的 mock facilitator，便于非 live 回归。

## `POST /agent/run`

`brain-py` 内置了 LangGraph Agent，并通过 `StructuredTool` 调用：

- `executor-ts /transfers/sign`
- `executor-ts /x402/fetch`
- `executor-ts /executions/submit`
- `executor-ts /user-operations/submit`

请求示例：

```json
{
  "input": "访问 x402 demo 资源，然后根据返回结果决定是否继续执行链上动作"
}
```
