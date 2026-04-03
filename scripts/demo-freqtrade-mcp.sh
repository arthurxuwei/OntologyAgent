#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

wait_for_url() {
  local url="$1"
  local name="$2"
  local user="${3:-}"
  local password="${4:-}"
  local max_retries="${5:-60}"

  for ((i=1; i<=max_retries; i+=1)); do
    if [[ -n "${user}" ]]; then
      if curl -fsS -u "${user}:${password}" "${url}" >/dev/null 2>&1; then
        echo "✅ ${name} is ready: ${url}"
        return 0
      fi
    else
      if curl -fsS "${url}" >/dev/null 2>&1; then
        echo "✅ ${name} is ready: ${url}"
        return 0
      fi
    fi
    sleep 1
  done

  echo "❌ ${name} failed to become ready: ${url}"
  return 1
}

export FREQTRADE_USERNAME="${FREQTRADE_USERNAME:-freqtrade}"
export FREQTRADE_PASSWORD="${FREQTRADE_PASSWORD:-freqtrade}"
export FREQTRADE_ALLOW_WRITE_ACTIONS="${FREQTRADE_ALLOW_WRITE_ACTIONS:-true}"

docker compose up -d --build

wait_for_url "http://localhost:8000/health" "brain-py"
wait_for_url "http://localhost:3000/health" "executor-ts"
wait_for_url "http://localhost:8080/api/v1/ping" "freqtrade-api" "${FREQTRADE_USERNAME}" "${FREQTRADE_PASSWORD}"

echo
echo "===> brain-py health"
curl -sS "http://localhost:8000/health"
echo

echo
echo "===> freqtrade ping"
curl -sS -u "${FREQTRADE_USERNAME}:${FREQTRADE_PASSWORD}" "http://localhost:8080/api/v1/ping"
echo

echo
echo "===> discovered Freqtrade MCP tools"
docker compose exec -T brain-py python - <<'PY'
import asyncio
import json
from freqtrade_mcp_client import FreqtradeMcpClient

async def main() -> None:
    client = FreqtradeMcpClient("http://freqtrade:8090/mcp/")
    tools = await client.list_tools()
    print(json.dumps({"tools": tools}, ensure_ascii=False))

asyncio.run(main())
PY

echo
echo "===> get_trading_status via MCP"
docker compose exec -T brain-py python - <<'PY'
import asyncio
import json
from freqtrade_mcp_client import FreqtradeMcpClient

async def main() -> None:
    client = FreqtradeMcpClient("http://freqtrade:8090/mcp/")
    result = await client.call_tool("get_trading_status", {})
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(main())
PY

echo
echo "===> Done"
