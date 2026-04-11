import json
import re
import subprocess
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import main


def _extract_inline_script(html: str) -> str:
    match = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    if match is None:
        raise AssertionError("expected inline script in chat page html")
    return match.group(1)


class FakeAutonomyController:
    def __init__(self) -> None:
        self.started = False
        self.start_calls: list[bool] = []
        self.stop_calls: list[bool] = []

    async def start(self, *, force: bool = False) -> None:
        self.start_calls.append(force)
        self.started = True

    async def stop(self, *, disable: bool = True) -> None:
        self.stop_calls.append(disable)
        self.started = False

    async def status(self) -> dict[str, object]:
        return {
            "enabled": self.started,
            "autostartConfigured": False,
            "running": self.started,
            "intervalSeconds": 60,
            "modelName": "gpt-4o-mini",
            "thresholds": {},
            "ledger": {
                "activeExecutions": [
                    {
                        "executionId": "exec-123",
                        "status": "running",
                    }
                ],
                "executionHistory": [
                    {
                        "executionId": "exec-122",
                        "status": "completed",
                    }
                ],
                "circuitBreaker": {
                    "state": "open",
                    "failureCount": 2,
                },
            },
        }

    async def tick(self) -> dict[str, object]:
        return {
            "context": {"wallet": {"balanceEth": "1.0"}},
            "decision": {"action": "hold"},
            "actionResult": {"action": "hold", "changedState": False},
        }


class FakeGraph:
    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        messages = list(payload["messages"])  # type: ignore[index]
        messages.append(AIMessage(content="interactive reply"))
        return {"messages": messages}


class FakeToolClient:
    def __init__(self, tools: list[str]) -> None:
        self._tools = tools

    async def list_tools(self) -> list[str]:
        return self._tools


class MainApiTests(unittest.TestCase):
    def setUp(self) -> None:
        main.get_session_store.cache_clear()
        main.get_chain_activity_store.cache_clear()

    def test_agent_sessions_support_multi_turn_chat(self) -> None:
        controller = FakeAutonomyController()
        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=FakeGraph()),
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["sessionId"]

                send_response = client.post(
                    f"/agent/sessions/{session_id}/messages",
                    json={"input": "你好，先帮我看下 guard"},
                )
                self.assertEqual(send_response.status_code, 200)
                body = send_response.json()
                self.assertEqual(body["sessionId"], session_id)
                self.assertEqual(body["output"], "interactive reply")
                self.assertGreaterEqual(body["messageCount"], 2)

                state_response = client.get(f"/agent/sessions/{session_id}")
                self.assertEqual(state_response.status_code, 200)
                self.assertEqual(state_response.json()["sessionId"], session_id)

    def test_chat_page_is_served(self) -> None:
        controller = FakeAutonomyController()
        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("OntologyAgent Console", response.text)
        self.assertIn("/agent/sessions", response.text)

    def test_chat_page_console_first_sections(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        for section_heading in [
            "Runtime",
            "Freqtrade",
            "Chain",
            "Recent Chain Action",
            "管家",
        ]:
            self.assertIn(section_heading, response.text)

    def test_chat_page_console_layout_classes(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        for marker in ["console-shell", "observability-grid", "detail-grid"]:
            self.assertIn(marker, response.text)
        normalized_html = " ".join(response.text.split())
        self.assertIn(
            "@media (max-width: 960px) { .topbar { padding: 16px; } .console-layout { gap: 16px; } .topbar-actions, .observability-grid, .detail-grid { grid-template-columns: 1fr; } .chat-panel { min-height: auto; } .composer-actions { flex-direction: column; align-items: stretch; }",
            normalized_html,
        )

    def test_chat_page_renders_runtime_freqtrade_chain_detail_targets(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        for marker in [
            'id="runtime-status"',
            'id="freqtrade-state"',
            'id="chain-identifier"',
            'id="execution-snapshot"',
            'id="warnings-panel"',
            'id="warnings-text"',
            'id="action-result"',
        ]:
            self.assertIn(marker, response.text)

    def test_chat_page_console_action_buttons_and_refresh_helpers(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        for marker in [
            'id="start-button"',
            'id="tick-button"',
            'id="stop-button"',
            "async function refreshDashboard",
        ]:
            self.assertIn(marker, response.text)

    def test_chat_page_registers_periodic_dashboard_refresh(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        self.assertIn("setInterval(refreshDashboard", script_text)

    def test_chat_page_refresh_dashboard_skips_overlapping_fetches(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        self.assertIn("let dashboardRefreshPromise = null;", script_text)
        self.assertIn("if (dashboardRefreshPromise) {", script_text)
        self.assertIn("return dashboardRefreshPromise;", script_text)
        self.assertIn("dashboardRefreshPromise = (async () => {", script_text)
        self.assertIn("dashboardRefreshPromise = null;", script_text)

    def test_tick_action_does_not_overwrite_runtime_card_before_health_refresh(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        node_result = subprocess.run(
            [
                "node",
                "-e",
                "const fs = require('fs');"
                "const scriptText = fs.readFileSync(0, 'utf8');"
                "const elements = new Map();"
                "const makeElement = () => ({"
                "textContent: '', style: {}, disabled: false, value: '/autonomy/status',"
                "appendChild() {}, append() {}, addEventListener() {}, focus() {},"
                "scrollTop: 0, scrollHeight: 0"
                "});"
                "global.document = {"
                "getElementById(id) {"
                "if (!elements.has(id)) elements.set(id, makeElement());"
                "return elements.get(id);"
                "},"
                "querySelectorAll() { return []; },"
                "createElement() { return makeElement(); },"
                "createTextNode(text) { return text; }"
                "};"
                "const healthPayload = {"
                "autonomy: { enabled: true, running: true, summary: { circuitState: 'closed' }, ledger: { healthStatus: 'ok', lastDecision: { action: 'buy' } } }"
                "};"
                "const tickPayload = {"
                "context: { wallet: { balanceEth: '1.0' } },"
                "decision: { action: 'hold' },"
                "actionResult: { action: 'hold', changedState: false }"
                "};"
                "let healthCalls = 0;"
                "global.fetch = async (url) => {"
                "if (url === '/health') {"
                "healthCalls += 1;"
                "if (healthCalls === 1) { return { ok: true, json: async () => healthPayload }; }"
                "return { ok: true, json: async () => await new Promise(() => {}) };"
                "}"
                "if (url === '/autonomy/tick') { return { ok: true, json: async () => tickPayload }; }"
                "throw new Error(`Unexpected URL: ${url}`);"
                "};"
                "global.setInterval = () => 0;"
                "(async () => {"
                "eval(scriptText);"
                "await refreshDashboard();"
                "const before = elements.get('runtime-status').textContent;"
                "await runGuardAction('/autonomy/tick', '执行理财子 Tick');"
                "const after = elements.get('runtime-status').textContent;"
                "process.stdout.write(JSON.stringify({ before, after, actionResult: elements.get('action-result').textContent }));"
                "})().catch((error) => { console.error(error); process.exit(1); });",
            ],
            input=script_text,
            text=True,
            capture_output=True,
        )
        self.assertEqual(node_result.returncode, 0, node_result.stderr)
        helper_output = json.loads(node_result.stdout)
        self.assertEqual(
            helper_output["before"],
            "运行状态: 运行中\n健康状态: ok\n熔断状态: closed\n最近建议: buy",
        )
        self.assertEqual(helper_output["after"], helper_output["before"])
        self.assertIn('"action": "hold"', helper_output["actionResult"])

    def test_chat_page_view_model_helpers_map_observability_payload_fields(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        script_text = _extract_inline_script(response.text)
        payloads = {
            "runtime": {
                "autonomy": {
                    "enabled": True,
                    "running": False,
                    "summary": {"circuitState": "open"},
                    "ledger": {
                        "healthStatus": "degraded",
                        "lastDecision": {"action": "hold"},
                        "activeExecutions": [{"executionId": "exec-123"}],
                        "executionHistory": [{"executionId": "exec-122"}],
                        "circuitBreaker": {"state": "open"},
                        "lastTickAt": "2026-04-10T10:00:00Z",
                    },
                }
            },
            "freqtrade": {
                "freqtradeStatus": {
                    "state": "running",
                    "runmode": "dry_run",
                    "exchange": "binance",
                    "strategy": "SimpleAgentStrategy",
                    "openTradeCount": 2,
                }
            },
            "chain": {
                "chainWallet": {"wallet": {"address": "0xabc"}},
                "recentChainAction": {
                    "tool": "chain_submit_execution",
                    "summary": {
                        "kind": "submit_execution",
                        "txHash": "0x123",
                        "valueEth": "0.5",
                    },
                },
            },
            "chainWrapped": {
                "chainWallet": {"result": {"wallet": {"address": "0xdef"}}},
                "recentChainAction": {
                    "tool": "chain_submit_execution",
                    "summary": {
                        "kind": "submit_execution",
                        "txHash": "0x456",
                        "valueEth": "0.25",
                    },
                },
            },
            "health": {
                "autonomy": {
                    "enabled": False,
                    "running": False,
                    "error": "autonomy unavailable",
                    "summary": {"circuitState": "open"},
                    "ledger": {
                        "healthStatus": "critical",
                        "lastError": "tick failed",
                        "circuitBreaker": {"state": "open"},
                    },
                },
                "chainError": "chain offline",
                "chainWalletError": "wallet unavailable",
                "freqtradeError": "freqtrade offline",
                "freqtradeStatus": {
                    "runningState": "unavailable",
                    "error": "snapshot unavailable",
                },
                "chainWallet": {"wallet": {}},
            },
            "healthWrapped": {
                "autonomy": {
                    "enabled": False,
                    "running": False,
                    "summary": {"circuitState": "closed"},
                    "ledger": {"healthStatus": "ok"},
                },
                "chainWallet": {"result": {"wallet": {"address": "0xdef"}}},
            },
        }
        node_result = subprocess.run(
            [
                "node",
                "-e",
                "const fs = require('fs');"
                "const scriptText = fs.readFileSync(0, 'utf8');"
                f"const payloads = {json.dumps(payloads)};"
                "const makeElement = () => ({"
                "textContent: '', style: {}, disabled: false, value: '/autonomy/status',"
                "appendChild() {}, append() {}, addEventListener() {}, focus() {},"
                "scrollTop: 0, scrollHeight: 0"
                "});"
                "global.document = {"
                "getElementById() { return makeElement(); },"
                "querySelectorAll() { return []; },"
                "createElement() { return makeElement(); }"
                "};"
                "global.fetch = async () => ({ ok: true, json: async () => ({}) });"
                "global.setInterval = () => 0;"
                "eval(scriptText);"
                "process.stdout.write(JSON.stringify({"
                "runtime: buildRuntimeViewModel(payloads.runtime),"
                "freqtrade: buildFreqtradeViewModel(payloads.freqtrade),"
                "chain: buildChainViewModel(payloads.chain),"
                "chainWrapped: buildChainViewModel(payloads.chainWrapped),"
                "chainFailed: buildChainViewModel(payloads.health),"
                "executionSnapshot: buildExecutionSnapshotViewModel(payloads.runtime),"
                "warnings: buildWarningsViewModel(payloads.health),"
                "warningsWrapped: buildWarningsViewModel(payloads.healthWrapped)"
                "}));",
            ],
            input=script_text,
            text=True,
            capture_output=True,
        )
        self.assertEqual(node_result.returncode, 0, node_result.stderr)
        helper_output = json.loads(node_result.stdout)
        self.assertEqual(
            helper_output["runtime"],
            {
                "running": "已启用，待运行",
                "health": "degraded",
                "circuitState": "open",
                "decision": "hold",
            },
        )
        self.assertEqual(
            helper_output["freqtrade"],
            {
                "running": "running | dry_run | binance | SimpleAgentStrategy",
                "openTrades": "2",
            },
        )
        self.assertEqual(
            helper_output["chain"],
            {
                "signerAddress": "0xabc",
                "recentAction": "submit_execution | value=0.5 ETH | tx=0x123",
            },
        )
        self.assertEqual(
            helper_output["chainWrapped"],
            {
                "signerAddress": "0xdef",
                "recentAction": "submit_execution | value=0.25 ETH | tx=0x456",
            },
        )
        self.assertEqual(
            helper_output["chainFailed"],
            {
                "signerAddress": "读取失败",
                "recentAction": "暂无",
            },
        )
        self.assertEqual(
            helper_output["executionSnapshot"],
            {
                "activeExecutions": "1",
                "executionHistory": "1",
                "circuitState": "open",
                "lastTickAt": "2026-04-10T10:00:00Z",
            },
        )
        self.assertEqual(
            helper_output["warnings"],
            [
                "Runtime health: critical",
                "Circuit breaker: open",
                "Chain wallet error: wallet unavailable",
                "Chain error: chain offline",
                "Freqtrade error: freqtrade offline",
                "Freqtrade error: snapshot unavailable",
                "Autonomy error: autonomy unavailable",
                "Autonomy error: tick failed",
            ],
        )
        self.assertEqual(helper_output["warningsWrapped"], [])

    def test_autonomy_management_endpoints_use_controller(self) -> None:
        controller = FakeAutonomyController()
        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                start_response = client.post("/autonomy/start")
                self.assertEqual(start_response.status_code, 200)
                self.assertTrue(start_response.json()["enabled"])
                self.assertEqual(
                    start_response.json()["summary"]["activeExecutionCount"], 1
                )

                tick_response = client.post("/autonomy/tick")
                self.assertEqual(tick_response.status_code, 200)
                self.assertEqual(tick_response.json()["decision"]["action"], "hold")

                stop_response = client.post("/autonomy/stop")
                self.assertEqual(stop_response.status_code, 200)
                self.assertFalse(stop_response.json()["enabled"])
                self.assertEqual(
                    stop_response.json()["summary"]["circuitState"], "open"
                )

        self.assertIn(True, controller.start_calls)
        self.assertIn(True, controller.stop_calls)

    def test_autonomy_status_exposes_active_execution_and_circuit_breaker(self) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                response = client.get("/autonomy/status")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["ledger"]["activeExecutions"][0]["executionId"], "exec-123"
        )
        self.assertEqual(body["ledger"]["circuitBreaker"]["state"], "open")
        self.assertEqual(body["summary"]["activeExecutionCount"], 1)

    def test_health_includes_autonomy_execution_summary(self) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(
                main,
                "get_chain_mcp_client",
                return_value=FakeToolClient(["chain_get_wallet_state"]),
            ),
            patch.object(
                main,
                "get_freqtrade_mcp_client",
                return_value=FakeToolClient(["freqtrade_status"]),
            ),
            patch.object(
                main, "get_chain_wallet_state", return_value={"balanceEth": "1.0"}
            ),
        ):
            with TestClient(main.app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["autonomy"]["ledger"]["activeExecutions"][0]["executionId"], "exec-123"
        )

    def test_health_includes_chain_and_freqtrade_status_fields(self) -> None:
        controller = FakeAutonomyController()

        class FakeChainClient:
            async def list_tools(self) -> list[str]:
                return ["chain_sign_transfer", "chain_get_wallet_state"]

        class FakeFreqtradeClient:
            async def list_tools(self) -> list[str]:
                return ["get_trading_status", "get_open_trades"]

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_chain_mcp_client", return_value=FakeChainClient()),
            patch.object(
                main, "get_freqtrade_mcp_client", return_value=FakeFreqtradeClient()
            ),
            patch.object(
                main,
                "get_chain_wallet_state",
                return_value={"wallet": {"address": "0xabc"}},
            ),
            patch.object(
                main,
                "get_freqtrade_status_snapshot",
                return_value={
                    "openTradeCount": 2,
                    "state": "running",
                    "runmode": "dry_run",
                    "exchange": "binance",
                    "strategy": "SimpleAgentStrategy",
                },
            ),
            patch.object(
                main,
                "get_chain_activity_store",
            ) as activity_store_factory,
        ):
            activity_store_factory.return_value.get.return_value = {
                "tool": "chain_sign_transfer",
                "summary": {"kind": "sign_transfer"},
            }
            with TestClient(main.app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["chainWallet"]["wallet"]["address"], "0xabc")
        self.assertEqual(payload["recentChainAction"]["tool"], "chain_sign_transfer")
        self.assertEqual(payload["freqtradeStatus"]["openTradeCount"], 2)
        self.assertEqual(payload["freqtradeStatus"]["state"], "running")

    def test_health_console_fields_include_expected_contract(self) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(
                main,
                "get_chain_mcp_client",
                return_value=FakeToolClient(["chain_get_wallet_state"]),
            ),
            patch.object(
                main,
                "get_freqtrade_mcp_client",
                return_value=FakeToolClient(["freqtrade_status"]),
            ),
            patch.object(
                main,
                "get_chain_wallet_state",
                return_value={"wallet": {"address": "0xabc", "balanceEth": "1.0"}},
            ),
            patch.object(
                main,
                "get_freqtrade_status_snapshot",
                return_value={"openTradeCount": 2, "state": "running"},
            ),
            patch.object(
                main,
                "get_chain_activity_store",
            ) as activity_store_factory,
        ):
            activity_store_factory.return_value.get.return_value = {
                "tool": "chain_sign_transfer",
                "summary": {"kind": "sign_transfer", "txHash": "0x123"},
            }
            with TestClient(main.app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("autonomy", payload)
        self.assertIn("freqtradeStatus", payload)
        self.assertIn("chainWallet", payload)
        self.assertIn("recentChainAction", payload)
        self.assertEqual(payload["autonomy"]["summary"]["activeExecutionCount"], 1)
        self.assertEqual(payload["freqtradeStatus"]["state"], "running")
        self.assertEqual(payload["chainWallet"]["wallet"]["address"], "0xabc")
        self.assertEqual(payload["recentChainAction"]["summary"]["txHash"], "0x123")

    def test_health_reports_chain_wallet_error_when_wallet_lookup_fails(self) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(
                main,
                "get_chain_mcp_client",
                return_value=FakeToolClient(["chain_get_wallet_state"]),
            ),
            patch.object(
                main,
                "get_freqtrade_mcp_client",
                return_value=FakeToolClient(["freqtrade_status"]),
            ),
            patch.object(
                main,
                "get_chain_wallet_state",
                side_effect=RuntimeError("wallet unavailable"),
            ),
        ):
            with TestClient(main.app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["chainWallet"])
        self.assertEqual(payload["chainWalletError"], "wallet unavailable")

    def test_autonomy_start_and_stop_summary_shape_matches_console_contract(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                start_response = client.post("/autonomy/start")
                stop_response = client.post("/autonomy/stop")

        self.assertEqual(start_response.status_code, 200)
        self.assertEqual(stop_response.status_code, 200)
        self.assertEqual(
            start_response.json()["summary"],
            {"activeExecutionCount": 1, "circuitState": "open"},
        )
        self.assertEqual(
            stop_response.json()["summary"],
            {"activeExecutionCount": 1, "circuitState": "open"},
        )

    def test_build_tools_exposes_chain_settlement_query_tools(self) -> None:
        tool_names = {tool.name for tool in main.build_tools()}

        self.assertIn("chain_get_transaction_receipt", tool_names)
        self.assertIn("chain_get_user_operation_status", tool_names)

    def test_health_recent_chain_action_summarizes_current_settlement_shape(
        self,
    ) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(
                main,
                "get_chain_mcp_client",
                return_value=FakeToolClient(["chain_get_wallet_state"]),
            ),
            patch.object(
                main,
                "get_freqtrade_mcp_client",
                return_value=FakeToolClient(["freqtrade_status"]),
            ),
            patch.object(
                main, "get_chain_wallet_state", return_value={"balanceEth": "1.0"}
            ),
        ):
            main.get_chain_activity_store().set(
                "chain_submit_execution",
                main._summarize_chain_result(
                    "chain_submit_execution",
                    {
                        "result": {
                            "execution": {"to": "0xabc", "valueEth": "0.25"},
                            "settlement": {
                                "identifier": "0xsettlement",
                                "kind": "submitted",
                                "status": "submitted",
                            },
                        }
                    },
                ),
            )

            with TestClient(main.app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        recent_chain_action = response.json()["recentChainAction"]
        self.assertEqual(recent_chain_action["summary"]["kind"], "submit_execution")
        self.assertEqual(recent_chain_action["summary"]["txHash"], "0xsettlement")
        self.assertEqual(recent_chain_action["summary"]["status"], "submitted")

    def test_health_recent_chain_action_summarizes_user_operation_target(self) -> None:
        controller = FakeAutonomyController()

        with (
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(
                main,
                "get_chain_mcp_client",
                return_value=FakeToolClient(["chain_get_wallet_state"]),
            ),
            patch.object(
                main,
                "get_freqtrade_mcp_client",
                return_value=FakeToolClient(["freqtrade_status"]),
            ),
            patch.object(
                main, "get_chain_wallet_state", return_value={"balanceEth": "1.0"}
            ),
        ):
            main.get_chain_activity_store().set(
                "chain_submit_user_operation",
                main._summarize_chain_result(
                    "chain_submit_user_operation",
                    {
                        "result": {
                            "userOperation": {
                                "target": "0xdef",
                                "userOpHash": "0xuserop123",
                            },
                            "settlement": {
                                "identifier": "0xuserop123",
                                "kind": "user-operation",
                                "status": "submitted",
                            },
                        }
                    },
                ),
            )

            with TestClient(main.app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        recent_chain_action = response.json()["recentChainAction"]
        self.assertEqual(
            recent_chain_action["summary"]["kind"], "submit_user_operation"
        )
        self.assertEqual(recent_chain_action["summary"]["target"], "0xdef")
        self.assertEqual(recent_chain_action["summary"]["userOpHash"], "0xuserop123")


if __name__ == "__main__":
    unittest.main()
