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

echo "===> Starting demo stack with mock-chain mode"
export EXECUTOR_MOCK_CHAIN="${EXECUTOR_MOCK_CHAIN:-true}"
export WHITELISTED_RECIPIENTS="${WHITELISTED_RECIPIENTS:-0x000000000000000000000000000000000000dEaD,0x1111111111111111111111111111111111111111}"
export DAILY_LIMIT="${DAILY_LIMIT:-2.0}"
export SINGLE_TX_CAP="${SINGLE_TX_CAP:-1.0}"
docker compose up -d --build

wait_for_url "http://localhost:8000/health" "brain-py"
wait_for_url "http://localhost:3000/health" "executor-ts"

echo
echo "===> 1) Demo /sign-transfer"
curl -sS -X POST "http://localhost:3000/sign-transfer" \
  -H "content-type: application/json" \
  -d '{
    "to": "0x000000000000000000000000000000000000dEaD",
    "amountEth": "0.01"
  }'
echo

echo
echo "===> 2) Demo /execute-swap (x402 auto-pay retry)"
curl -sS -X POST "http://localhost:3000/execute-swap" \
  -H "content-type: application/json" \
  -d '{
    "apiUrl": "http://brain-py:8000/mock-x402",
    "apiMethod": "POST",
    "apiBody": {
      "tokenIn": "ETH",
      "tokenOut": "USDC"
    },
    "payment": {
      "to": "0x1111111111111111111111111111111111111111",
      "amountEth": "0.001",
      "maxRetries": 1
    }
  }'
echo

echo
echo "===> Done"
echo "Tip: set EXECUTOR_MOCK_CHAIN=false + PRIVATE_KEY=... to use real chain tx."
