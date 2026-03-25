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

require_env() {
  local env_name="$1"
  if [[ -z "${!env_name:-}" ]]; then
    echo "❌ Missing required env: ${env_name}"
    exit 1
  fi
}

export EXECUTOR_MOCK_CHAIN="${EXECUTOR_MOCK_CHAIN:-false}"
export RPC_URL="${RPC_URL:-https://base-sepolia-rpc.publicnode.com}"
export CHAIN_ID="${CHAIN_ID:-84532}"
export DAILY_LIMIT="${DAILY_LIMIT:-2.0}"
export SINGLE_TX_CAP="${SINGLE_TX_CAP:-1.0}"
export X402_NETWORK="${X402_NETWORK:-eip155:84532}"
export X402_PRICE="${X402_PRICE:-\$0.01}"
export X402_FACILITATOR_URL="${X402_FACILITATOR_URL:-https://x402.org/facilitator}"

if [[ "${EXECUTOR_MOCK_CHAIN}" == "true" ]]; then
  echo "===> Starting demo stack in mock-chain mode"
  export PRIVATE_KEY="${PRIVATE_KEY:-0x59c6995e998f97a5a0044966f0945382d8f6d5b40f5f0c6d9c0a0f6f6b6b6b6b}"
  export DEMO_SIGN_TRANSFER_TO="${DEMO_SIGN_TRANSFER_TO:-0x000000000000000000000000000000000000dEaD}"
  export DEMO_X402_PAYMENT_TO="${DEMO_X402_PAYMENT_TO:-0x1111111111111111111111111111111111111111}"
  export X402_PAY_TO="${X402_PAY_TO:-${DEMO_X402_PAYMENT_TO}}"
  export X402_FACILITATOR_URL="http://brain-py:8000/x402/mock-facilitator"
else
  echo "===> Starting demo stack on live testnet"
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

wait_for_url "http://localhost:8000/health" "brain-py"
wait_for_url "http://localhost:3000/health" "executor-ts"

echo
echo "===> 1) Demo /transfers/sign"
curl -sS -X POST "http://localhost:3000/transfers/sign" \
  -H "content-type: application/json" \
  -d "{
    \"to\": \"${DEMO_SIGN_TRANSFER_TO}\",
    \"amountEth\": \"0.01\"
  }"
echo

echo
echo "===> 2) Demo /x402/fetch"
curl -sS -X POST "http://localhost:3000/x402/fetch" \
  -H "content-type: application/json" \
  -d "{
    \"url\": \"http://brain-py:8000/x402/demo-resource\",
    \"method\": \"GET\"
  }"
echo

echo
echo "===> 3) Demo /executions/submit"
curl -sS -X POST "http://localhost:3000/executions/submit" \
  -H "content-type: application/json" \
  -d "{
    \"to\": \"${DEMO_SIGN_TRANSFER_TO}\",
    \"valueEth\": \"0.001\",
    \"data\": \"0x\"
  }"
echo

echo
echo "===> Done"
echo "RPC_URL=${RPC_URL}"
echo "CHAIN_ID=${CHAIN_ID}"
echo "X402_NETWORK=${X402_NETWORK}"
echo "X402_FACILITATOR_URL=${X402_FACILITATOR_URL}"
