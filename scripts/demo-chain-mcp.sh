#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

wait_for_url() {
  local url="$1"
  local name="$2"
  local max_retries="${3:-60}"

  for ((i=1; i<=max_retries; i+=1)); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      echo "✅ ${name} is ready: ${url}"
      return 0
    fi
    sleep 1
  done

  echo "❌ ${name} failed to become ready: ${url}"
  return 1
}

wait_for_chain_mcp() {
  local max_retries="${1:-60}"

  for ((i=1; i<=max_retries; i+=1)); do
    if docker compose exec -T agent python - <<'PY' >/dev/null 2>&1
import asyncio
from chain_mcp_client import ChainMcpClient

async def main():
    tools = await ChainMcpClient("http://chain-mcp:8091/mcp/").list_tools()
    assert "chain_sign_transfer" in tools

asyncio.run(main())
PY
    then
      echo "✅ chain MCP tools are ready"
      return 0
    fi
    sleep 1
  done

  echo "❌ chain MCP tools failed to become ready"
  return 1
}

wait_for_x402_mock() {
  local max_retries="${1:-60}"

  for ((i=1; i<=max_retries; i+=1)); do
    if docker compose exec -T agent python - <<'PY' >/dev/null 2>&1
import httpx

response = httpx.get("http://x402-mock:8000/health", timeout=5)
assert response.status_code == 200
PY
    then
      echo "✅ x402-mock is ready"
      return 0
    fi
    sleep 1
  done

  echo "❌ x402-mock failed to become ready"
  return 1
}

wait_for_x402_seller() {
  local max_retries="${1:-60}"

  for ((i=1; i<=max_retries; i+=1)); do
    if docker compose exec -T agent python - <<'PY' >/dev/null 2>&1
import httpx

response = httpx.get("http://x402-seller:8000/health", timeout=5)
assert response.status_code == 200
PY
    then
      echo "✅ x402-seller is ready"
      return 0
    fi
    sleep 1
  done

  echo "❌ x402-seller failed to become ready"
  return 1
}

require_env() {
  local env_name="$1"
  if [[ -z "${!env_name:-}" ]]; then
    echo "❌ Missing required env: ${env_name}"
    exit 1
  fi
}

export CHAIN_MOCK="${CHAIN_MOCK:-false}"
export RPC_URL="${RPC_URL:-https://base-sepolia-rpc.publicnode.com}"
export CHAIN_ID="${CHAIN_ID:-84532}"
export X402_NETWORK="${X402_NETWORK:-eip155:84532}"
export X402_PRICE="${X402_PRICE:-\$0.01}"
export X402_FACILITATOR_URL="${X402_FACILITATOR_URL:-https://x402.org/facilitator}"
export DEMO_X402_RESOURCE_PATH="${DEMO_X402_RESOURCE_PATH:-/x402/demo-resource}"
export DEMO_X402_PAYMENT_PREFERENCE="${DEMO_X402_PAYMENT_PREFERENCE:-standard}"

if [[ "${CHAIN_MOCK}" == "true" ]]; then
  echo "===> Starting chain MCP demo in mock-chain mode"
  export PRIVATE_KEY="${PRIVATE_KEY:-0x59c6995e998f97a5a0044966f0945382d8f6d5b40f5f0c6d9c0a0f6f6b6b6b6b}"
  export DEMO_SIGN_TRANSFER_TO="${DEMO_SIGN_TRANSFER_TO:-0x000000000000000000000000000000000000dEaD}"
  export DEMO_X402_PAYMENT_TO="${DEMO_X402_PAYMENT_TO:-0x1111111111111111111111111111111111111111}"
  export X402_PAY_TO="${X402_PAY_TO:-${DEMO_X402_PAYMENT_TO}}"
  export X402_FACILITATOR_URL="http://x402-mock:8000/x402/facilitator"
else
  echo "===> Starting chain MCP demo on live testnet"
  require_env "PRIVATE_KEY"
  require_env "DEMO_SIGN_TRANSFER_TO"
  require_env "DEMO_X402_PAYMENT_TO"
  export X402_PAY_TO="${X402_PAY_TO:-${DEMO_X402_PAYMENT_TO}}"
fi

if [[ -n "${WHITELISTED_RECIPIENTS:-}" ]]; then
  export WHITELISTED_RECIPIENTS="${WHITELISTED_RECIPIENTS},${DEMO_SIGN_TRANSFER_TO},${DEMO_X402_PAYMENT_TO}"
else
  export WHITELISTED_RECIPIENTS="${DEMO_SIGN_TRANSFER_TO},${DEMO_X402_PAYMENT_TO}"
fi

docker compose up -d --build

wait_for_url "http://localhost:8000/health" "agent"
wait_for_chain_mcp
wait_for_x402_seller
if [[ "${CHAIN_MOCK}" == "true" ]]; then
  wait_for_x402_mock
fi

echo
echo "===> agent health"
curl -sS "http://localhost:8000/health"
echo

echo
echo "===> chain MCP tools"
docker compose exec -T agent python - <<'PY'
import asyncio
import json
from chain_mcp_client import ChainMcpClient

async def main():
    tools = await ChainMcpClient("http://chain-mcp:8091/mcp/").list_tools()
    print(json.dumps({"tools": tools}, ensure_ascii=False))

asyncio.run(main())
PY

echo
echo "===> chain_sign_transfer"
docker compose exec -T agent python - <<PY
import asyncio
import json
from chain_mcp_client import ChainMcpClient

async def main():
    result = await ChainMcpClient("http://chain-mcp:8091/mcp/").call_tool(
        "chain_sign_transfer",
        {
            "to": "${DEMO_SIGN_TRANSFER_TO}",
            "amountEth": "0.01",
        },
    )
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(main())
PY

echo
echo "===> chain_x402_fetch"
docker compose exec -T agent python - <<PY
import asyncio
import json
from chain_mcp_client import ChainMcpClient

async def main():
    result = await ChainMcpClient("http://chain-mcp:8091/mcp/").call_tool(
        "chain_x402_fetch",
        {
            "url": "http://x402-seller:8000${DEMO_X402_RESOURCE_PATH}",
            "method": "GET",
            "paymentPreference": "${DEMO_X402_PAYMENT_PREFERENCE}",
        },
    )
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(main())
PY

echo
echo "===> chain_submit_execution"
docker compose exec -T agent python - <<PY
import asyncio
import json
from chain_mcp_client import ChainMcpClient

async def main():
    result = await ChainMcpClient("http://chain-mcp:8091/mcp/").call_tool(
        "chain_submit_execution",
        {
            "to": "${DEMO_SIGN_TRANSFER_TO}",
            "valueEth": "0.001",
            "data": "0x",
        },
    )
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(main())
PY

echo
echo "===> chain_submit_user_operation"
docker compose exec -T agent python - <<PY
import asyncio
import json
from chain_mcp_client import ChainMcpClient

async def main():
    result = await ChainMcpClient("http://chain-mcp:8091/mcp/").call_tool(
        "chain_submit_user_operation",
        {
            "target": "${DEMO_SIGN_TRANSFER_TO}",
            "maxCostEth": "0.01",
            "raw": {
                "sender": "0x123"
            },
        },
    )
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(main())
PY

echo
echo "===> Done"
