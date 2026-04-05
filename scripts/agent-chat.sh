#!/usr/bin/env bash

set -euo pipefail

AGENT_BASE_URL="${AGENT_BASE_URL:-http://localhost:8000}"

create_session() {
  curl -fsS -X POST "${AGENT_BASE_URL}/agent/sessions"
}

extract_json_field() {
  local field="$1"
  python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$field"
}

print_json() {
  python3 -m json.tool
}

send_message() {
  local session_id="$1"
  local message="$2"
  local payload
  payload="$(python3 -c 'import json,sys; print(json.dumps({"input": sys.argv[1]}))' "$message")"
  curl -fsS \
    -X POST \
    -H "content-type: application/json" \
    -d "$payload" \
    "${AGENT_BASE_URL}/agent/sessions/${session_id}/messages"
}

session_json="$(create_session)"
session_id="$(printf '%s' "$session_json" | extract_json_field "sessionId")"

echo "Connected to ${AGENT_BASE_URL}"
echo "Session: ${session_id}"
echo "Commands:"
echo "  /wealth-status 查看理财子状态"
echo "  /wealth-start  启动理财子"
echo "  /wealth-stop   停止理财子"
echo "  /wealth-tick   执行一轮理财子检查"
echo "  /exit          quit"
echo

while true; do
  printf 'you> '
  if ! IFS= read -r line; then
    echo
    break
  fi

  if [[ -z "${line}" ]]; then
    continue
  fi

  case "${line}" in
    /exit|exit|quit)
      break
      ;;
    /wealth-status|/guard-status)
      curl -fsS "${AGENT_BASE_URL}/autonomy/status" | print_json
      continue
      ;;
    /wealth-start|/guard-start)
      curl -fsS -X POST "${AGENT_BASE_URL}/autonomy/start" | print_json
      continue
      ;;
    /wealth-stop|/guard-stop)
      curl -fsS -X POST "${AGENT_BASE_URL}/autonomy/stop" | print_json
      continue
      ;;
    /wealth-tick|/guard-tick)
      curl -fsS -X POST "${AGENT_BASE_URL}/autonomy/tick" | print_json
      continue
      ;;
  esac

  response_json="$(send_message "${session_id}" "${line}")"
  printf 'agent> %s\n\n' "$(printf '%s' "$response_json" | extract_json_field "output")"
done
