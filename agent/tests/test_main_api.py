import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import main


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
            "ledger": {},
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


class MainApiTests(unittest.TestCase):
    def setUp(self) -> None:
        main.get_session_store.cache_clear()

    def test_agent_sessions_support_multi_turn_chat(self) -> None:
        controller = FakeAutonomyController()
        with patch.object(main, "get_autonomy_controller", return_value=controller), patch.object(
            main, "get_agent_graph", return_value=FakeGraph()
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

    def test_autonomy_management_endpoints_use_controller(self) -> None:
        controller = FakeAutonomyController()
        with patch.object(main, "get_autonomy_controller", return_value=controller):
            with TestClient(main.app) as client:
                start_response = client.post("/autonomy/start")
                self.assertEqual(start_response.status_code, 200)
                self.assertTrue(start_response.json()["enabled"])

                tick_response = client.post("/autonomy/tick")
                self.assertEqual(tick_response.status_code, 200)
                self.assertEqual(tick_response.json()["decision"]["action"], "hold")

                stop_response = client.post("/autonomy/stop")
                self.assertEqual(stop_response.status_code, 200)
                self.assertFalse(stop_response.json()["enabled"])

        self.assertIn(True, controller.start_calls)
        self.assertIn(True, controller.stop_calls)

    def test_health_includes_chain_and_freqtrade_status_fields(self) -> None:
        controller = FakeAutonomyController()

        class FakeChainClient:
            async def list_tools(self) -> list[str]:
                return ["chain_sign_transfer", "chain_get_wallet_state"]

        class FakeFreqtradeClient:
            async def list_tools(self) -> list[str]:
                return ["get_trading_status", "get_open_trades"]

        with patch.object(main, "get_autonomy_controller", return_value=controller), patch.object(
            main, "get_chain_mcp_client", return_value=FakeChainClient()
        ), patch.object(main, "get_freqtrade_mcp_client", return_value=FakeFreqtradeClient()), patch.object(
            main,
            "get_chain_wallet_state",
            return_value={"wallet": {"address": "0xabc"}},
        ), patch.object(
            main,
            "get_freqtrade_status_snapshot",
            return_value={
                "openTradeCount": 2,
                "state": "running",
                "runmode": "dry_run",
                "exchange": "binance",
                "strategy": "SimpleAgentStrategy",
            },
        ), patch.object(
            main,
            "get_chain_activity_store",
        ) as activity_store_factory:
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


if __name__ == "__main__":
    unittest.main()
