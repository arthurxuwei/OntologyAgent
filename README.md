# OntologyAgent

项目包含两个主要目录：

- `brain-py`：Python 业务逻辑（FastAPI）
- `executor-ts`：TypeScript 链上执行器（Fastify + ethers）

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
- `executor-ts` 健康检查：`http://localhost:3000/health`
- `executor-ts` 接口：`POST /sign-transfer`、`POST /execute-swap`

## 一键 curl 演示

在根目录执行：

```bash
./scripts/demo-curl.sh
```

脚本会自动：

- 启动 `docker compose`
- 等待 `brain-py` 与 `executor-ts` 就绪
- 调用 `POST /sign-transfer`
- 调用 `POST /execute-swap`，演示 `402 -> 自动链上支付 -> 重试成功`

默认直连 Sepolia 测试链，并要求你显式提供测试私钥与收款地址。建议先准备少量测试 ETH，再执行：

```bash
PRIVATE_KEY=0x... \
DEMO_SIGN_TRANSFER_TO=0x... \
DEMO_X402_PAYMENT_TO=0x... \
./scripts/demo-curl.sh
```

如需切回 mock 模式：

```bash
EXECUTOR_MOCK_CHAIN=true ./scripts/demo-curl.sh
```

## 可选环境变量

- `EXECUTOR_PORT`：执行器端口（默认 `3000`）
- `PRIVATE_KEY`：执行器签名私钥（必须配置才能签名/发交易）
- `RPC_URL`：链 RPC 地址（默认 `https://ethereum-sepolia-rpc.publicnode.com`）
- `CHAIN_ID`：期望连接的链 ID（默认 `11155111`，即 Sepolia）
- `DAILY_LIMIT`：每日可签名总额度（ETH，默认 `2.0`）
- `SINGLE_TX_CAP`：单笔交易额度（ETH，默认 `1.0`，且受硬编码上限约束）
- `WHITELISTED_RECIPIENTS`：额外白名单地址，逗号分隔
- `X402_MAX_RETRIES`：x402 自动支付重试次数（默认 `1`）
- `EXECUTOR_MOCK_CHAIN`：是否使用模拟链执行（默认 `false`）
- `BUNDLER_RPC_URL`：ERC-4337 Bundler RPC（可选）
- `ENTRY_POINT_ADDRESS`：ERC-4337 EntryPoint（默认 `0x0576...1B57`）
- `EXECUTOR_BASE_URL`：`brain-py` 调用 `executor-ts` 的基地址（默认 `http://executor-ts:3000`）
- `EXECUTOR_TIMEOUT_SECONDS`：`brain-py` 调用 `executor-ts` 的超时时间秒数（默认 `20`）
- `OPENAI_API_KEY`：`brain-py` LangChain Agent 使用的模型密钥
- `BRAIN_AGENT_MODEL`：`brain-py` Agent 模型名（默认 `gpt-4o-mini`）
- `DEMO_SIGN_TRANSFER_TO`：演示脚本里 `/sign-transfer` 使用的测试链地址
- `DEMO_X402_PAYMENT_TO`：演示脚本里 x402 支付使用的测试链地址

## `POST /sign-transfer`

签名原生转账（不广播）：

```json
{
  "to": "0x000000000000000000000000000000000000dEaD",
  "amountEth": "0.01"
}
```

返回签名后的原始交易、tx hash，以及策略引擎快照。

当 `EXECUTOR_MOCK_CHAIN=false` 时，会先校验当前 RPC 的 `chainId` 是否与 `CHAIN_ID` 一致，避免误连到主网。

## `POST /execute-swap`

执行一次带 x402 自动支付重试的上游 API 请求，并可选择执行链上 swap 交易或提交 ERC-4337 UserOperation：

```json
{
  "apiUrl": "https://example.com/swap/quote",
  "apiMethod": "POST",
  "apiBody": {
    "tokenIn": "ETH",
    "tokenOut": "USDC"
  },
  "payment": {
    "to": "0x1111111111111111111111111111111111111111",
    "amountEth": "0.001",
    "maxRetries": 1
  },
  "swapTx": {
    "to": "0x000000000000000000000000000000000000dEaD",
    "valueEth": "0",
    "data": "0x"
  }
}
```

可选字段 `userOperation` 存在时，会调用 Bundler 的 `eth_sendUserOperation`。

> 演示脚本里，`apiUrl` 指向 `brain-py` 的 `POST /mock-x402`，用于稳定复现 `402` 支付重试流程。

`userOperation` 格式示例：

```json
{
  "userOperation": {
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
}
```

## `POST /agent/run`（brain-py）

`brain-py` 内置了 LangGraph Agent，并通过 `StructuredTool` 调用 `executor-ts` 的 `/sign-transfer` 与 `/execute-swap`。  
系统提示词固定为：

> 你是一个金融助理，只能通过调用 TS 执行器接口来移动资金。

请求示例：

```json
{
  "input": "给 0x000000000000000000000000000000000000dEaD 签名转账 0.01 ETH"
}
```
