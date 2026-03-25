# OntologyAgent

项目包含两个主要目录：

- `brain-py`：Python 业务逻辑（FastAPI）
- `executor-ts`：TypeScript 链上执行器（Fastify + ethers）

## 同时启动（Docker Compose）

在仓库根目录执行：

```bash
docker compose up --build
```

启动后：

- `brain-py` 健康检查：`http://localhost:8000/health`
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

默认开启 `EXECUTOR_MOCK_CHAIN=true`（不会广播真实链上交易）。  
如需真实链执行，可设置：

```bash
EXECUTOR_MOCK_CHAIN=false PRIVATE_KEY=0x... ./scripts/demo-curl.sh
```

## 可选环境变量

- `EXECUTOR_PORT`：执行器端口（默认 `3000`）
- `PRIVATE_KEY`：执行器签名私钥（必须配置才能签名/发交易）
- `RPC_URL`：链 RPC 地址（默认 `https://ethereum-rpc.publicnode.com`）
- `DAILY_LIMIT`：每日可签名总额度（ETH，默认 `2.0`）
- `SINGLE_TX_CAP`：单笔交易额度（ETH，默认 `1.0`，且受硬编码上限约束）
- `WHITELISTED_RECIPIENTS`：额外白名单地址，逗号分隔
- `X402_MAX_RETRIES`：x402 自动支付重试次数（默认 `1`）
- `EXECUTOR_MOCK_CHAIN`：是否使用模拟链执行（默认 `false`，脚本演示时会设为 `true`）
- `BUNDLER_RPC_URL`：ERC-4337 Bundler RPC（可选）
- `ENTRY_POINT_ADDRESS`：ERC-4337 EntryPoint（默认 `0x0576...1B57`）

## `POST /sign-transfer`

签名原生转账（不广播）：

```json
{
  "to": "0x000000000000000000000000000000000000dEaD",
  "amountEth": "0.01"
}
```

返回签名后的原始交易、tx hash，以及策略引擎快照。

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
