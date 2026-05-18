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

    def test_dashboard_chat_and_admin_web_entries_are_separate(self) -> None:
        class FakeLedgerResponse:
            text = "<html><title>Chief Ledger</title></html>"

            def raise_for_status(self):
                return None

        client = TestClient(main.app)

        home_response = client.get("/")
        dashboard_response = client.get("/dashboard")
        chat_response = client.get("/chat")
        with patch.object(
            main.httpx.AsyncClient,
            "get",
            return_value=FakeLedgerResponse(),
        ):
            admin_response = client.get("/admin")

        self.assertEqual(home_response.status_code, 200)
        self.assertIn("Agent Wallet · MVP Dashboard", home_response.text)
        self.assertIn("if (!registered) return <window.RegistrationScreen />", home_response.text)
        self.assertIn("if (!claimed)    return <window.ClaimScreen />", home_response.text)
        self.assertIn("const [mockState, setMockStateState]   = React.useState('day1');", home_response.text)
        self.assertNotIn("<window.MockStateToggle />", home_response.text)
        self.assertIn("fetch(`/dashboard/data${emailQuery}`)", home_response.text)
        self.assertIn("fetch(`/dashboard/claimable-agents?", home_response.text)
        self.assertNotIn("claimAgent('agentA')", home_response.text)
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertIn("Agent Wallet · MVP Dashboard", dashboard_response.text)
        self.assertEqual(chat_response.status_code, 200)
        self.assertIn("OntologyAgent Console", chat_response.text)
        self.assertEqual(admin_response.status_code, 200)
        self.assertIn("Chief Ledger", admin_response.text)

    def test_dashboard_ledger_state_proxies_ledger_service(self) -> None:
        class FakeLedgerResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"accounts": [{"agentId": "agentA"}], "escrows": []}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url):
                self.url = url
                return FakeLedgerResponse()

        with patch.object(main.httpx, "AsyncClient", FakeAsyncClient):
            response = TestClient(main.app).get("/dashboard/ledger-state")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["accounts"][0]["agentId"], "agentA")

    def test_dashboard_data_returns_dashboard_ready_ledger_shape(self) -> None:
        class FakeLedgerResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "accounts": [
                        {
                            "agentId": "buyer",
                            "asset": "USDC",
                            "availableAtomic": "1500000",
                            "lockedAtomic": "250000",
                            "updatedAt": "2026-05-14T10:00:00+00:00",
                        },
                        {
                            "agentId": "seller",
                            "asset": "USDC",
                            "availableAtomic": "750000",
                            "lockedAtomic": "0",
                            "updatedAt": "2026-05-14T10:05:00+00:00",
                        },
                    ],
                    "entries": [
                        {
                            "entryId": "entry_credit",
                            "entryType": "credit",
                            "agentId": "buyer",
                            "availableDeltaAtomic": "2000000",
                            "lockedDeltaAtomic": "0",
                            "reason": "operator funding",
                            "createdAt": "2026-05-14T09:00:00+00:00",
                        },
                        {
                            "entryId": "entry_lock",
                            "entryType": "escrow_lock",
                            "agentId": "buyer",
                            "availableDeltaAtomic": "-500000",
                            "lockedDeltaAtomic": "500000",
                            "escrowId": "escrow_1",
                            "reason": "escrow created",
                            "createdAt": "2026-05-14T09:30:00+00:00",
                        },
                    ],
                    "escrows": [
                        {
                            "escrowId": "escrow_1",
                            "buyerAgentId": "buyer",
                            "sellerAgentId": "seller",
                            "amountAtomic": "500000",
                            "status": "locked",
                            "description": "analysis task",
                        }
                    ],
                    "onrampSessions": [],
                }

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url):
                self.url = url
                return FakeLedgerResponse()

        with patch.object(main.httpx, "AsyncClient", FakeAsyncClient):
            response = TestClient(main.app).get("/dashboard/data")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["defaultAgentId"], "buyer")
        self.assertEqual(set(payload["agents"].keys()), {"buyer", "seller"})
        buyer = payload["agents"]["buyer"]
        self.assertEqual(buyer["agent"]["name"], "buyer")
        self.assertEqual(buyer["balance"]["available"], 1.5)
        self.assertEqual(buyer["balance"]["locked"], 0.25)
        self.assertEqual(buyer["balance"]["lifetimeIn"], 2.0)
        self.assertEqual(buyer["balance"]["lifetimeOut"], 0.5)
        self.assertEqual(buyer["transactions"][0]["counterparty"], "seller")
        self.assertEqual(buyer["transactions"][0]["amount"], 0.5)
        self.assertEqual(buyer["transactions"][0]["direction"], "out")

    def test_dashboard_claimable_agents_are_filtered_by_owner_email(self) -> None:
        ledger_state = {
            "accounts": [
                {
                    "agentId": "agent_alpha",
                    "asset": "USDC",
                    "availableAtomic": "2500000",
                    "lockedAtomic": "0",
                },
                {
                    "agentId": "agent_beta",
                    "asset": "USDC",
                    "availableAtomic": "1000000",
                    "lockedAtomic": "0",
                },
            ],
            "entries": [],
            "escrows": [],
            "onrampSessions": [],
        }
        bindings = [
            {
                "agentId": "agent_alpha",
                "agentName": "Alpha Research",
                "email": "Owner@Example.com",
                "walletAddress": "0x1111111111111111111111111111111111111111",
                "circleWalletId": "circle-alpha",
                "updatedAt": "2026-05-14T07:57:06.148Z",
            },
            {
                "agentId": "agent_beta",
                "agentName": "Beta Research",
                "email": "other@example.com",
                "walletAddress": "0x2222222222222222222222222222222222222222",
                "circleWalletId": "circle-beta",
                "updatedAt": "2026-05-14T07:58:06.148Z",
            },
        ]

        with patch.object(main, "fetch_ledger_state", return_value=ledger_state), patch.object(
            main, "load_agent_wallet_bindings", return_value=bindings
        ):
            response = TestClient(main.app).get(
                "/dashboard/claimable-agents?email=owner@example.com"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["email"], "owner@example.com")
        self.assertEqual(len(payload["agents"]), 1)
        candidate = payload["agents"][0]
        self.assertEqual(candidate["agentId"], "agent_alpha")
        self.assertEqual(candidate["agentName"], "Alpha Research")
        self.assertEqual(candidate["ownerEmail"], "owner@example.com")
        self.assertEqual(candidate["claimStatus"], "unclaimed")
        self.assertEqual(candidate["dashboard"]["balance"]["available"], 2.5)

    def test_dashboard_claimable_agents_exclude_already_claimed_ids(self) -> None:
        ledger_state = {
            "accounts": [{"agentId": "agent_alpha", "asset": "USDC"}],
            "entries": [],
            "escrows": [],
        }
        bindings = [
            {
                "agentId": "agent_alpha",
                "agentName": "Alpha Research",
                "email": "owner@example.com",
                "walletAddress": "0x1111111111111111111111111111111111111111",
            }
        ]

        with patch.object(main, "fetch_ledger_state", return_value=ledger_state), patch.object(
            main, "load_agent_wallet_bindings", return_value=bindings
        ):
            response = TestClient(main.app).get(
                "/dashboard/claimable-agents?email=owner@example.com&claimed=agent_alpha"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["agents"], [])

    def test_dashboard_data_can_be_filtered_by_owner_email(self) -> None:
        ledger_state = {
            "accounts": [
                {"agentId": "agent_alpha", "asset": "USDC", "availableAtomic": "2500000"},
                {"agentId": "agent_beta", "asset": "USDC", "availableAtomic": "1000000"},
            ],
            "entries": [],
            "escrows": [],
        }
        bindings = [
            {
                "agentId": "agent_alpha",
                "agentName": "Alpha Research",
                "email": "owner@example.com",
                "walletAddress": "0x1111111111111111111111111111111111111111",
            },
            {
                "agentId": "agent_beta",
                "agentName": "Beta Research",
                "email": "other@example.com",
                "walletAddress": "0x2222222222222222222222222222222222222222",
            },
        ]

        with patch.object(main, "fetch_ledger_state", return_value=ledger_state), patch.object(
            main, "load_agent_wallet_bindings", return_value=bindings
        ):
            response = TestClient(main.app).get("/dashboard/data?email=owner@example.com")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload["agents"].keys()), {"agent_alpha"})
        self.assertEqual(payload["defaultAgentId"], "agent_alpha")
        self.assertEqual(payload["agents"]["agent_alpha"]["agent"]["name"], "Alpha Research")

    def test_ledger_api_routes_proxy_ledger_service_for_admin_page(self) -> None:
        class FakeLedgerResponse:
            status_code = 200
            content = b'{"ok":true}'
            headers = {"content-type": "application/json"}

        class FakeAsyncClient:
            calls = []

            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def request(self, method, url, content=None, headers=None):
                self.calls.append((method, url, content, headers))
                return FakeLedgerResponse()

        with patch.object(main.httpx, "AsyncClient", FakeAsyncClient):
            response = TestClient(main.app).post(
                "/ledger/wallets/get-or-create",
                json={"agentId": "agentA", "agentName": "Agent A"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        method, url, content, _headers = FakeAsyncClient.calls[-1]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/ledger/wallets/get-or-create"))
        self.assertIn(b"agentA", content)


if __name__ == "__main__":
    unittest.main()
