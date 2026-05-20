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

export CHAIN_MOCK=false
export RPC_URL="${RPC_URL:-https://base-sepolia-rpc.publicnode.com}"
export CHAIN_ID="${CHAIN_ID:-84532}"
export X402_NETWORK="${X402_NETWORK:-eip155:84532}"
export X402_FACILITATOR_URL="${X402_FACILITATOR_URL:-https://x402.org/facilitator}"
export X402_USDC_SINGLE_CAP="${X402_USDC_SINGLE_CAP:-1.0}"
export X402_USDC_DAILY_CAP="${X402_USDC_DAILY_CAP:-2.0}"

export SIMPLESCRAPER_ENDPOINT="${SIMPLESCRAPER_ENDPOINT:-https://api.simplescraper.io/v1/extract}"
export SIMPLESCRAPER_TARGET_URL="${SIMPLESCRAPER_TARGET_URL:-https://example.com}"
export SIMPLESCRAPER_PAY_TO="${SIMPLESCRAPER_PAY_TO:-0x6C01bea8570FDFDa471992d68e5C284A69A6B46d}"

require_env "PRIVATE_KEY"

if [[ -n "${WHITELISTED_RECIPIENTS:-}" ]]; then
  export WHITELISTED_RECIPIENTS="${WHITELISTED_RECIPIENTS},${SIMPLESCRAPER_PAY_TO}"
else
  export WHITELISTED_RECIPIENTS="${SIMPLESCRAPER_PAY_TO}"
fi

echo "===> Starting live Base Sepolia stack for Simplescraper x402"
docker compose up -d --build

wait_for_url "http://localhost:8000/health" "agent"
wait_for_url "http://localhost:8091/health" "chain"

echo
echo "===> Brain health"
curl -sS "http://localhost:8000/health"
echo

echo
echo "===> Simplescraper live x402 fetch"
curl -sS \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"${SIMPLESCRAPER_ENDPOINT}\",\"method\":\"POST\",\"body\":{\"url\":\"${SIMPLESCRAPER_TARGET_URL}\",\"markdown\":true}}" \
  "http://localhost:8091/x402/fetch"
echo

echo
echo "===> Done"
echo "RPC_URL=${RPC_URL}"
echo "CHAIN_ID=${CHAIN_ID}"
echo "X402_NETWORK=${X402_NETWORK}"
echo "X402_FACILITATOR_URL=${X402_FACILITATOR_URL}"
echo "SIMPLESCRAPER_ENDPOINT=${SIMPLESCRAPER_ENDPOINT}"
echo "SIMPLESCRAPER_TARGET_URL=${SIMPLESCRAPER_TARGET_URL}"
