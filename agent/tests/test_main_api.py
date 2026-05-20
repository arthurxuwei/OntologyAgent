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
        main.clear_tool_cache()
        main.get_agent_graph.cache_clear()
        main.get_session_store.cache_clear()

    def test_health_returns_pure_runtime_shape(self) -> None:
        class Catalog:
            skills = []

        with patch.object(main, "get_skill_catalog", return_value=Catalog()), patch.object(
            main, "build_tools", return_value=[]
        ):
            response = TestClient(main.app).get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["toolTransport"], "rest")
        self.assertEqual(payload["actionServices"]["ledger"], "http://ledger:8092")
        self.assertNotIn("autonomy", payload)
        self.assertNotIn("chainWallet", payload)
        self.assertNotIn("mc" + "pServers", payload)

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

    def test_root_and_chat_serve_agent_console(self) -> None:
        client = TestClient(main.app)

        home_response = client.get("/")
        chat_response = client.get("/chat")

        self.assertEqual(home_response.status_code, 200)
        self.assertIn("OntologyAgent Console", home_response.text)
        self.assertEqual(chat_response.status_code, 200)
        self.assertIn("OntologyAgent Console", chat_response.text)

    def test_dashboard_and_admin_are_not_agent_owned_routes(self) -> None:
        client = TestClient(main.app)

        self.assertEqual(client.get("/dashboard").status_code, 404)
        self.assertEqual(client.get("/dashboard/data").status_code, 404)
        self.assertEqual(client.get("/admin").status_code, 404)


if __name__ == "__main__":
    unittest.main()
