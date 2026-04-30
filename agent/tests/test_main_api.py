import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class FakeGraph:
    async def ainvoke(self, payload):
        from langchain_core.messages import AIMessage

        return {"messages": [*payload["messages"], AIMessage(content="hello")]}


class MainApiTests(unittest.TestCase):
    def setUp(self) -> None:
        main.clear_discovered_tool_cache()
        main.get_agent_graph.cache_clear()
        main.get_session_store.cache_clear()

    def test_health_returns_pure_runtime_shape(self) -> None:
        class Catalog:
            skills = []

            def server_names(self):
                return {"ledger"}

        class Runtime:
            def health(self):
                return {"ledger": {"status": "ok"}}

        with patch.object(main, "get_skill_catalog", return_value=Catalog()), patch.object(
            main, "get_mcp_runtime", return_value=Runtime()
        ), patch.object(main, "build_tools", return_value=[]):
            response = TestClient(main.app).get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["mcpServers"], ["ledger"])
        self.assertNotIn("autonomy", payload)
        self.assertNotIn("chainWallet", payload)

    def test_agent_session_message_uses_graph(self) -> None:
        with patch.object(main, "get_agent_graph", return_value=FakeGraph()):
            client = TestClient(main.app)
            session_id = client.post("/agent/sessions").json()["sessionId"]
            response = client.post(
                f"/agent/sessions/{session_id}/messages",
                json={"input": "hi"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["output"], "hello")
        self.assertEqual(payload["messageCount"], 2)

    def test_removed_agent_wallet_routes_return_404(self) -> None:
        response = TestClient(main.app).get("/agent-wallet/state")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
