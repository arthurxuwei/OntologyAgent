import asyncio
import base64
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

import main


class LedgerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path(__file__).resolve().parents[2] / ".codex-tmp"
        temp_root.mkdir(exist_ok=True)
        self.temp_dir = temp_root / f"ledger-test-{uuid.uuid4().hex}"
        self.temp_dir.mkdir()
        self.state_path = str(self.temp_dir / "ledger.json")
        self.previous_state_path = os.environ.get("LEDGER_STATE_PATH")
        self.previous_coinbase_mock = os.environ.get("COINBASE_ONRAMP_MOCK")
        self.previous_coinbase_key_id = os.environ.get("COINBASE_API_KEY_ID")
        self.previous_coinbase_private_key = os.environ.get("COINBASE_API_PRIVATE_KEY")
        self.previous_chain_record_enabled = os.environ.get("LEDGER_CHAIN_RECORD_ENABLED")
        self.previous_chain_record_require_success = os.environ.get(
            "LEDGER_CHAIN_RECORD_REQUIRE_SUCCESS"
        )
        self.previous_settlement_enabled = os.environ.get("LEDGER_SETTLEMENT_ENABLED")
        self.previous_settlement_require_success = os.environ.get(
            "LEDGER_SETTLEMENT_REQUIRE_SUCCESS"
        )
        self.previous_settlement_mcp_url = os.environ.get("LEDGER_SETTLEMENT_MCP_URL")
        self.previous_wallet_mcp_url = os.environ.get("LEDGER_WALLET_MCP_URL")
        os.environ["LEDGER_STATE_PATH"] = self.state_path
        os.environ["LEDGER_CHAIN_RECORD_ENABLED"] = "false"
        os.environ["LEDGER_CHAIN_RECORD_REQUIRE_SUCCESS"] = "false"
        os.environ["LEDGER_SETTLEMENT_ENABLED"] = "false"
        os.environ["LEDGER_SETTLEMENT_REQUIRE_SUCCESS"] = "false"
        main.get_store.cache_clear()
        main.get_coinbase_onramp_client.cache_clear()
        main.get_chain_recorder.cache_clear()
        main.get_ledger_settlement_client.cache_clear()
        main.get_ledger_wallet_client.cache_clear()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.get_store.cache_clear()
        main.get_ledger_settlement_client.cache_clear()
        main.get_ledger_wallet_client.cache_clear()
        if self.previous_state_path is None:
            os.environ.pop("LEDGER_STATE_PATH", None)
        else:
            os.environ["LEDGER_STATE_PATH"] = self.previous_state_path
        if self.previous_coinbase_mock is None:
            os.environ.pop("COINBASE_ONRAMP_MOCK", None)
        else:
            os.environ["COINBASE_ONRAMP_MOCK"] = self.previous_coinbase_mock
        self._restore_env("COINBASE_API_KEY_ID", self.previous_coinbase_key_id)
        self._restore_env("COINBASE_API_PRIVATE_KEY", self.previous_coinbase_private_key)
        self._restore_env("LEDGER_CHAIN_RECORD_ENABLED", self.previous_chain_record_enabled)
        self._restore_env(
            "LEDGER_CHAIN_RECORD_REQUIRE_SUCCESS",
            self.previous_chain_record_require_success,
        )
        self._restore_env("LEDGER_SETTLEMENT_ENABLED", self.previous_settlement_enabled)
        self._restore_env(
            "LEDGER_SETTLEMENT_REQUIRE_SUCCESS",
            self.previous_settlement_require_success,
        )
        self._restore_env("LEDGER_SETTLEMENT_MCP_URL", self.previous_settlement_mcp_url)
        self._restore_env("LEDGER_WALLET_MCP_URL", self.previous_wallet_mcp_url)
        main.get_coinbase_onramp_client.cache_clear()
        main.get_chain_recorder.cache_clear()
        main.get_ledger_settlement_client.cache_clear()
        main.get_ledger_wallet_client.cache_clear()

    def _restore_env(self, name: str, previous: str | None) -> None:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous

    def test_health_returns_ok(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_coinbase_auth_supports_cdp_key_id_and_base64_private_key(self) -> None:
        private_key = ed25519.Ed25519PrivateKey.generate()
        seed = private_key.private_bytes_raw()
        os.environ["COINBASE_API_KEY_ID"] = "test-api-key-id"
        os.environ["COINBASE_API_PRIVATE_KEY"] = base64.b64encode(seed).decode("ascii")

        auth = main.CoinbaseAuth(
            bearer_token=None,
            api_key_id=os.environ["COINBASE_API_KEY_ID"],
            api_private_key=os.environ["COINBASE_API_PRIVATE_KEY"],
        )
        token = auth.bearer_for(method="POST", host="api.developer.coinbase.com", path="/onramp/v1/token")
        header = jwt.get_unverified_header(token)
        payload = jwt.decode(token, options={"verify_signature": False})

        self.assertEqual(header["alg"], "EdDSA")
        self.assertEqual(header["kid"], "test-api-key-id")
        self.assertEqual(payload["iss"], "cdp")
        self.assertEqual(payload["sub"], "test-api-key-id")
        self.assertEqual(payload["uri"], "POST api.developer.coinbase.com/onramp/v1/token")

    def test_mcp_endpoint_initializes_with_ledger_app_lifespan(self) -> None:
        with TestClient(main.app) as client:
            response = client.get("/mcp/")

        self.assertNotEqual(response.status_code, 500)

    def test_root_redirects_to_dashboard(self) -> None:
        response = self.client.get("/", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/dashboard")

    def test_admin_serves_ledger_management_page(self) -> None:
        response = self.client.get("/admin")

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Chief Ledger", html)
        self.assertIn('rel="icon"', html)
        self.assertIn('class="brand-mark"', html)
        self.assertIn("Chief Ledger logo", html)
        self.assertIn('id="ledger-state"', html)
        self.assertIn('id="wallet-form"', html)
        self.assertNotIn('id="credit-form"', html)
        self.assertNotIn("Credit Account", html)
        self.assertIn('id="onramp-form"', html)
        self.assertIn('id="onramp-confirm-form"', html)
        self.assertNotIn('id="escrow-form"', html)
        self.assertNotIn("Create Escrow", html)
        self.assertIn('id="settlement-form"', html)
        self.assertIn("/ledger/state", html)
        self.assertIn("/onramp/sessions", html)
        self.assertIn("/ledger/escrows", html)
        self.assertIn('{ label: "Email"', html)

    def test_dashboard_serves_user_dashboard_page(self) -> None:
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Agent Wallet · MVP Dashboard", html)
        self.assertIn("if (!registered) return <window.RegistrationScreen />", html)
        self.assertIn("if (!claimed)    return <window.ClaimScreen />", html)
        self.assertIn("const [mockState, setMockStateState]   = React.useState('day1');", html)
        self.assertNotIn("<window.MockStateToggle />", html)
        self.assertIn("fetch(`/dashboard/data${emailQuery}`)", html)
        self.assertIn("fetch(`/dashboard/claimable-agents?", html)
        self.assertNotIn("claimAgent('agentA')", html)

    def test_dashboard_data_returns_email_scoped_ledger_accounts(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_alpha",
            agent_name="Alpha Research",
            email="Owner@Example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-alpha",
        )
        store.bind_account_wallet(
            agent_id="agent_beta",
            agent_name="Beta Research",
            email="other@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle-beta",
        )
        store.credit(
            agent_id="agent_alpha",
            amount_atomic="2500000",
            reason="operator funding",
            metadata={},
        )
        store.create_escrow(
            buyer_agent_id="agent_alpha",
            seller_agent_id="agent_beta",
            amount_atomic="500000",
            task_id="task_123",
            description="Research task",
            metadata={},
        )

        response = self.client.get("/dashboard/data?email=owner@example.com")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "ledger")
        self.assertEqual(payload["defaultAgentId"], "agent_alpha")
        self.assertEqual(set(payload["agents"].keys()), {"agent_alpha"})
        alpha = payload["agents"]["agent_alpha"]
        self.assertEqual(alpha["agent"]["name"], "Alpha Research")
        self.assertEqual(alpha["agent"]["ownerEmail"], "owner@example.com")
        self.assertEqual(alpha["balance"]["available"], 2.0)
        self.assertEqual(alpha["balance"]["locked"], 0.5)
        self.assertEqual(alpha["balance"]["lifetimeIn"], 2.5)
        self.assertEqual(alpha["balance"]["lifetimeOut"], 0.5)
        self.assertEqual(alpha["transactions"][0]["counterparty"], "agent_beta")
        self.assertEqual(alpha["transactions"][0]["status"], "locked")

    def test_dashboard_claimable_agents_come_from_unclaimed_email_accounts(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_alpha",
            agent_name="Alpha Research",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-alpha",
        )
        store.bind_account_wallet(
            agent_id="agent_beta",
            agent_name="Beta Research",
            email="owner@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle-beta",
        )
        store.bind_account_wallet(
            agent_id="agent_other",
            agent_name="Other Research",
            email="other@example.com",
            wallet_address="0x3333333333333333333333333333333333333333",
            circle_wallet_id="circle-other",
        )
        store.credit(
            agent_id="agent_beta",
            amount_atomic="1250000",
            reason="operator funding",
            metadata={},
        )

        response = self.client.get(
            "/dashboard/claimable-agents?email=OWNER@example.com&claimed=agent_alpha"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["email"], "owner@example.com")
        self.assertEqual(payload["source"], "ledger-accounts")
        self.assertEqual(len(payload["agents"]), 1)
        candidate = payload["agents"][0]
        self.assertEqual(candidate["agentId"], "agent_beta")
        self.assertEqual(candidate["agentName"], "Beta Research")
        self.assertEqual(candidate["ownerEmail"], "owner@example.com")
        self.assertEqual(candidate["claimStatus"], "unclaimed")
        self.assertEqual(candidate["dashboard"]["balance"]["available"], 1.25)

    def test_management_page_helpers_render_and_call_ledger_api(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node is required to execute ledger page helpers")

        response = self.client.get("/admin")

        self.assertEqual(response.status_code, 200)
        script = response.text.split("<script>", 1)[1].split("</script>", 1)[0]
        import subprocess

        node_result = subprocess.run(
            [
                "node",
                "-e",
                "const fs = require('fs');"
                "const scriptText = fs.readFileSync(0, 'utf8');"
                "const elements = new Map();"
                "const listeners = new Map();"
                "const fetchCalls = [];"
                "global.window = { openCalls: [], open(url, target, features) { this.openCalls.push({ url, target, features }); return null; } };"
                "const makeElement = (id = '') => ({"
                "id, textContent: '', innerHTML: '', value: '', disabled: false, children: [], style: {},"
                "appendChild(child) { this.children.push(child); if (!this.value && child.value) this.value = child.value; },"
                "replaceChildren(...children) { this.children = children; this.value = ''; },"
                "addEventListener(event, handler) { listeners.set(`${id}:${event}`, handler.name || 'anonymous'); },"
                "focus() {}"
                "});"
                "global.document = {"
                "getElementById(id) { if (!elements.has(id)) elements.set(id, makeElement(id)); return elements.get(id); },"
                "querySelectorAll() { return []; },"
                "createElement(tag) { return makeElement(tag); },"
                "createTextNode(text) { return { textContent: text, innerHTML: String(text) }; }"
                "};"
                "global.fetch = async (url, options = {}) => {"
                "fetchCalls.push({ url, method: options.method || 'GET', body: options.body || null });"
                "if (url === '/ledger/state') return { ok: true, json: async () => ({"
                "accounts: [{ agentId: 'agent_buyer', email: 'buyer@example.com', walletAddress: '0x1111111111111111111111111111111111111111', circleUsdcBalance: '1.98', availableAtomic: '5000000', lockedAtomic: '3000000' }],"
                "entries: [{ entryId: 'entry_1', entryType: 'credit', agentId: 'agent_buyer' }],"
                "escrows: [{ escrowId: 'escrow_1', buyerAgentId: 'agent_buyer', sellerAgentId: 'agent_seller', amountAtomic: '3000000', status: 'locked' }],"
                "onrampSessions: [{ sessionId: 'onramp_1', agentId: 'agentA', paymentAmount: '10.00', status: 'created', onrampUrl: 'https://pay.coinbase.com/buy/select-asset?sessionToken=abc' }]"
                "}) };"
                "if (url === '/ledger/wallets/get-or-create') return { ok: true, json: async () => ({"
                "wallet: { walletAddress: '0x1111111111111111111111111111111111111111', circleWalletId: 'circle-wallet-1' },"
                "account: { agentId: 'agent_research', availableAtomic: '0', lockedAtomic: '0' }"
                "}) };"
                "if (url === '/onramp/sessions') return { ok: true, json: async () => ({"
                "sessionId: 'onramp_2', agentId: 'agentA', paymentAmount: '10.00', status: 'created', onrampUrl: 'https://pay.coinbase.com/buy/select-asset?sessionToken=def'"
                "}) };"
                "if (url === '/onramp/sessions/onramp_1/confirm') return { ok: true, json: async () => ({"
                "sessionId: 'onramp_1', agentId: 'agentA', paymentAmount: '10.00', creditedAmountAtomic: '10000000', status: 'credited'"
                "}) };"
                "return { ok: true, json: async () => ({ ok: true }) };"
                "};"
                "(async () => {"
                "eval(scriptText);"
                "await refreshLedgerState();"
                "elements.get('wallet-agent-name').value = 'Research Agent';"
                "elements.get('wallet-agent-id').value = 'agent_research';"
                "elements.get('wallet-email').value = 'agent@example.com';"
                "elements.get('wallet-circle-wallet-id').value = 'circle-wallet-1';"
                "elements.get('wallet-address').value = '0x1111111111111111111111111111111111111111';"
                "await getOrCreateWallet({ preventDefault() {} });"
                "elements.get('onramp-agent-id').value = 'agentA';"
                "elements.get('onramp-destination-address').value = '0x742d35Cc6634C0532925a3b844Bc454e4438f44e';"
                "elements.get('onramp-payment-amount').value = '10.00';"
                "elements.get('onramp-idempotency-key').value = 'fund-agentA-10';"
                "await createOnrampSession({ preventDefault() {} });"
                "elements.get('onramp-session-select').value = 'onramp_1';"
                "elements.get('onramp-provider-order-id').value = 'coinbase_order_123';"
                "elements.get('onramp-confirm-amount').value = '10000000';"
                "elements.get('onramp-confirm-tx-hash').value = '0xabc123';"
                "await confirmOnrampSession({ preventDefault() {} });"
                "process.stdout.write(JSON.stringify({"
                "stateHtml: elements.get('ledger-state').innerHTML,"
                "stateText: elements.get('ledger-state').textContent,"
                "escrowSelection: selectedEscrowId(),"
                "onrampSelection: selectedOnrampSessionId(),"
                "walletListener: listeners.get('wallet-form:submit'),"
                "onrampListener: listeners.get('onramp-form:submit'),"
                "onrampConfirmListener: listeners.get('onramp-confirm-form:submit'),"
                "settlementListener: listeners.get('settlement-form:submit'),"
                "walletCall: fetchCalls.find((call) => call.url === '/ledger/wallets/get-or-create'),"
                "onrampCall: fetchCalls.find((call) => call.url === '/onramp/sessions'),"
                "confirmCall: fetchCalls.find((call) => call.url === '/onramp/sessions/onramp_1/confirm'),"
                "openCall: window.openCalls[0],"
                "walletOutput: elements.get('wallet-output').textContent,"
                "onrampOutput: elements.get('onramp-output').textContent"
                "}));"
                "})().catch((error) => { console.error(error); process.exit(1); });",
            ],
            input=script,
            text=True,
            capture_output=True,
        )

        self.assertEqual(node_result.returncode, 0, node_result.stderr)
        import json

        output = json.loads(node_result.stdout)
        self.assertIn("Accounts", output["stateHtml"])
        self.assertIn("1", output["stateHtml"])
        self.assertIn("Ledger Available", output["stateHtml"])
        self.assertIn("5,000,000", output["stateHtml"])
        self.assertIn("Ledger Locked", output["stateHtml"])
        self.assertIn("3,000,000", output["stateHtml"])
        self.assertIn("Circle USDC Balance", output["stateHtml"])
        self.assertIn("1.98", output["stateHtml"])
        self.assertIn("agent_buyer", output["stateHtml"])
        self.assertIn("Email", output["stateHtml"])
        self.assertIn("buyer@example.com", output["stateHtml"])
        self.assertIn("0x1111111111111111111111111111111111111111", output["stateHtml"])
        self.assertIn("agent_seller", output["stateHtml"])
        self.assertIn("escrow_1", output["stateHtml"])
        self.assertIn("onramp_1", output["stateHtml"])
        self.assertIn("entry_1", output["stateHtml"])
        self.assertNotIn("{", output["stateHtml"])
        self.assertNotIn('"accounts"', output["stateHtml"])
        self.assertEqual(output["escrowSelection"], "escrow_1")
        self.assertEqual(output["onrampSelection"], "onramp_1")
        self.assertEqual(output["walletListener"], "getOrCreateWallet")
        self.assertEqual(output["onrampListener"], "createOnrampSession")
        self.assertEqual(output["onrampConfirmListener"], "confirmOnrampSession")
        self.assertEqual(output["settlementListener"], "preventSettlementSubmit")
        self.assertEqual(output["walletCall"]["method"], "POST")
        self.assertEqual(
            json.loads(output["walletCall"]["body"]),
            {
                "agentName": "Research Agent",
                "agentId": "agent_research",
                "email": "agent@example.com",
                "circleWalletId": "circle-wallet-1",
                "walletAddress": "0x1111111111111111111111111111111111111111",
            },
        )
        self.assertEqual(output["onrampCall"]["method"], "POST")
        self.assertEqual(
            json.loads(output["onrampCall"]["body"]),
            {
                "agentId": "agentA",
                "destinationAddress": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
                "paymentAmount": "10.00",
                "idempotencyKey": "fund-agentA-10",
            },
        )
        self.assertEqual(output["confirmCall"]["method"], "POST")
        self.assertEqual(
            json.loads(output["confirmCall"]["body"]),
            {
                "providerOrderId": "coinbase_order_123",
                "amountAtomic": "10000000",
                "txHash": "0xabc123",
            },
        )
        self.assertEqual(output["openCall"]["target"], "_blank")
        self.assertIn("https://pay.coinbase.com/buy/select-asset", output["openCall"]["url"])
        self.assertIn("agent_research", output["walletOutput"])
        self.assertIn("onramp_1", output["onrampOutput"])

    def test_credit_creates_account_and_entry(self) -> None:
        response = self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )

        self.assertEqual(response.status_code, 200)
        account = response.json()["account"]
        self.assertEqual(account["agentId"], "agent_buyer")
        self.assertEqual(account["availableAtomic"], "5000000")
        self.assertEqual(account["lockedAtomic"], "0")

        state = self.client.get("/ledger/state").json()
        self.assertEqual(len(state["entries"]), 1)
        self.assertEqual(state["entries"][0]["entryType"], "credit")

    def test_wallet_get_or_create_creates_zero_balance_ledger_account(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {
                    "circleWalletId": "circle-wallet-1",
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "mode": "circle",
                    "binding": {
                        "agentName": request.agentName,
                        "agentId": request.agentId,
                        "email": request.email,
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "circleWalletId": "circle-wallet-1",
                        "circleWalletSetId": "circle-wallet-set",
                        "blockchain": "BASE-SEPOLIA",
                        "mode": "circle",
                        "updatedAt": main.now_iso(),
                    },
                }

        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post(
                "/ledger/wallets/get-or-create",
                json={
                    "agentName": "Research Agent",
                    "agentId": "agent_research",
                    "email": "agent@example.com",
                    "circleWalletId": "circle-wallet-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["wallet"]["circleWalletId"], "circle-wallet-1")
        self.assertEqual(payload["wallet"]["binding"]["agentId"], "agent_research")
        self.assertEqual(payload["account"]["agentId"], "agent_research")
        self.assertEqual(payload["account"]["agentName"], "Research Agent")
        self.assertEqual(payload["account"]["email"], "agent@example.com")
        self.assertEqual(payload["account"]["availableAtomic"], "0")
        self.assertEqual(payload["account"]["lockedAtomic"], "0")

        state = self.client.get("/ledger/state").json()
        self.assertEqual(len(state["accounts"]), 1)
        self.assertEqual(state["accounts"][0]["agentId"], "agent_research")
        self.assertEqual(state["accounts"][0]["agentName"], "Research Agent")
        self.assertEqual(state["accounts"][0]["email"], "agent@example.com")
        self.assertEqual(
            state["accounts"][0]["walletAddress"],
            "0x1111111111111111111111111111111111111111",
        )
        self.assertEqual(state["accounts"][0]["circleWalletId"], "circle-wallet-1")
        self.assertEqual(state["entries"], [])

    def test_ledger_state_includes_circle_usdc_balance_for_bound_accounts(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                assert circle_wallet_id == "circle-wallet-1"
                return {"balances": {"USDC": "1.98"}}

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_research",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            state = self.client.get("/ledger/state").json()

        self.assertEqual(state["accounts"][0]["circleUsdcBalance"], "1.98")

    def test_wallet_get_or_create_requires_circle_binding_agent_id_to_match_request(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {
                    "binding": {
                        "agentId": "other_agent",
                    },
                }

        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post(
                "/ledger/wallets/get-or-create",
                json={
                    "agentName": "Research Agent",
                    "agentId": "agent_research",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "circle wallet binding agentId mismatch")
        self.assertEqual(self.client.get("/ledger/state").json()["accounts"], [])

    def test_wallet_get_or_create_mcp_tool_creates_ledger_account(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {
                    "circleWalletId": "circle-wallet-1",
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "mode": "circle",
                    "binding": {
                        "agentName": request.agentName,
                        "agentId": request.agentId,
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "circleWalletId": "circle-wallet-1",
                        "circleWalletSetId": "circle-wallet-set",
                        "blockchain": "BASE-SEPOLIA",
                        "mode": "circle",
                        "updatedAt": main.now_iso(),
                    },
                }

        tool = getattr(main, "agent_wallet_get_or_create_tool", None)
        self.assertIsNotNone(tool)
        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            result = asyncio.run(
                tool(
                    agentName="Research Agent",
                    agentId="agent_research",
                    circleWalletId="circle-wallet-1",
                )
            )

        self.assertEqual(result["account"]["agentId"], "agent_research")
        self.assertEqual(self.client.get("/ledger/state").json()["accounts"][0]["agentId"], "agent_research")

    def test_create_onramp_session_persists_coinbase_hosted_url(self) -> None:
        os.environ["COINBASE_ONRAMP_MOCK"] = "true"
        main.get_coinbase_onramp_client.cache_clear()

        response = self.client.post(
            "/onramp/sessions",
            json={
                "agentId": "agentA",
                "destinationAddress": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
                "paymentAmount": "10.00",
                "idempotencyKey": "fund-agentA-10",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], "agentA")
        self.assertEqual(payload["provider"], "coinbase")
        self.assertEqual(payload["status"], "created")
        self.assertEqual(payload["destinationNetwork"], "base")
        self.assertEqual(payload["purchaseCurrency"], "USDC")
        self.assertTrue(payload["onrampUrl"].startswith("https://pay.coinbase.com/buy/select-asset"))

        state = self.client.get("/ledger/state").json()
        self.assertEqual(len(state["onrampSessions"]), 1)
        self.assertEqual(state["onrampSessions"][0]["idempotencyKey"], "fund-agentA-10")

    def test_create_onramp_session_reuses_idempotency_key(self) -> None:
        os.environ["COINBASE_ONRAMP_MOCK"] = "true"
        main.get_coinbase_onramp_client.cache_clear()
        request = {
            "agentId": "agentA",
            "destinationAddress": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
            "paymentAmount": "10.00",
            "idempotencyKey": "fund-agentA-10",
        }

        first = self.client.post("/onramp/sessions", json=request).json()
        second = self.client.post("/onramp/sessions", json=request).json()

        self.assertEqual(first["sessionId"], second["sessionId"])
        self.assertEqual(len(self.client.get("/ledger/state").json()["onrampSessions"]), 1)

    def test_confirm_onramp_credits_ledger_once(self) -> None:
        os.environ["COINBASE_ONRAMP_MOCK"] = "true"
        main.get_coinbase_onramp_client.cache_clear()
        session = self.client.post(
            "/onramp/sessions",
            json={
                "agentId": "agentA",
                "destinationAddress": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
                "paymentAmount": "10.00",
                "idempotencyKey": "fund-agentA-10",
            },
        ).json()

        request = {
            "providerOrderId": "coinbase_order_123",
            "amountAtomic": "10000000",
            "txHash": "0xabc123",
        }
        first = self.client.post(
            f"/onramp/sessions/{session['sessionId']}/confirm",
            json=request,
        )
        second = self.client.post(
            f"/onramp/sessions/{session['sessionId']}/confirm",
            json=request,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["status"], "credited")
        self.assertEqual(second.json()["status"], "credited")
        state = self.client.get("/ledger/state").json()
        credit_entries = [
            entry for entry in state["entries"]
            if entry["reason"] == "coinbase_onramp_confirmed"
        ]
        self.assertEqual(len(credit_entries), 1)
        self.assertEqual(state["accounts"][0]["agentId"], "agentA")
        self.assertEqual(state["accounts"][0]["availableAtomic"], "10000000")
        self.assertEqual(
            credit_entries[0]["metadata"]["onrampSessionId"],
            session["sessionId"],
        )

    def test_confirm_onramp_rejects_non_positive_atomic_amount(self) -> None:
        os.environ["COINBASE_ONRAMP_MOCK"] = "true"
        main.get_coinbase_onramp_client.cache_clear()
        session = self.client.post(
            "/onramp/sessions",
            json={
                "agentId": "agentA",
                "destinationAddress": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
                "paymentAmount": "10.00",
                "idempotencyKey": "fund-agentA-10",
            },
        ).json()

        response = self.client.post(
            f"/onramp/sessions/{session['sessionId']}/confirm",
            json={"providerOrderId": "coinbase_order_123", "amountAtomic": "0"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "amountAtomic must be a positive integer string")
        state = self.client.get("/ledger/state").json()
        self.assertEqual(state["accounts"], [])

    def test_create_escrow_moves_buyer_available_to_locked(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )

        response = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
                "taskId": "task_123",
                "description": "Research task",
            },
        )

        self.assertEqual(response.status_code, 200)
        escrow = response.json()["escrow"]
        self.assertEqual(escrow["status"], "locked")
        self.assertEqual(escrow["amountAtomic"], "3000000")

        accounts = {
            item["agentId"]: item for item in self.client.get("/ledger/state").json()["accounts"]
        }
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "2000000")
        self.assertEqual(accounts["agent_buyer"]["lockedAtomic"], "3000000")

    def test_chain_record_is_persisted_for_escrow_lock_when_enabled(self) -> None:
        class FakeRecorder:
            enabled = True

            async def submit(self, *, event_type, escrow, entries, payload):
                current = main.now_iso()
                return main.LedgerChainRecord(
                    recordId="chainrec_test",
                    eventType=event_type,
                    status="submitted",
                    chainMcpUrl="http://chain.test/mcp/",
                    recorderAddress="0x000000000000000000000000000000000000dEaD",
                    txHash="0xtesttx",
                    mode="mock",
                    escrowId=escrow.escrowId if escrow is not None else None,
                    entryIds=[entry.entryId for entry in entries],
                    payload=payload,
                    toolResult={
                        "execution": {
                            "txHash": "0xtesttx",
                            "mode": "mock",
                        }
                    },
                    createdAt=current,
                    updatedAt=current,
                )

        with patch.object(main, "get_chain_recorder", return_value=FakeRecorder()):
            self.client.post(
                "/ledger/accounts/agent_buyer/credit",
                json={"amountAtomic": "5000000", "reason": "demo funding"},
            )
            response = self.client.post(
                "/ledger/escrows",
                json={
                    "buyerAgentId": "agent_buyer",
                    "sellerAgentId": "agent_seller",
                    "amountAtomic": "3000000",
                    "taskId": "task_123",
                    "description": "Research task",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["chainRecord"]["txHash"], "0xtesttx")
        state = self.client.get("/ledger/state").json()
        self.assertEqual(len(state["chainRecords"]), 2)
        lock_record = state["chainRecords"][1]
        self.assertEqual(lock_record["eventType"], "escrow_lock")
        self.assertEqual(lock_record["status"], "submitted")
        self.assertEqual(lock_record["payload"]["escrow"]["taskId"], "task_123")

    def test_create_escrow_rejects_insufficient_available_balance(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "1000000", "reason": "demo funding"},
        )

        response = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "insufficient available balance")
        state = self.client.get("/ledger/state").json()
        self.assertEqual(state["escrows"], [])
        self.assertEqual(state["accounts"][0]["availableAtomic"], "1000000")
        self.assertEqual(state["accounts"][0]["lockedAtomic"], "0")

    def test_release_escrow_moves_locked_funds_to_seller_available(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        escrow = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
            },
        ).json()["escrow"]

        response = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["escrow"]["status"], "released")
        accounts = {
            item["agentId"]: item for item in self.client.get("/ledger/state").json()["accounts"]
        }
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "2000000")
        self.assertEqual(accounts["agent_buyer"]["lockedAtomic"], "0")
        self.assertEqual(accounts["agent_seller"]["availableAtomic"], "3000000")

    def test_release_escrow_persists_settlement_record_when_enabled(self) -> None:
        class FakeSettlementClient:
            enabled = True

            async def submit_release(self, escrow):
                current = main.now_iso()
                return main.LedgerSettlementRecord(
                    recordId="settle_test",
                    eventType="escrow_release",
                    status="submitted",
                    chainMcpUrl="http://chain.test/mcp/",
                    escrowId=escrow.escrowId,
                    fromAgentId=escrow.buyerAgentId,
                    toAgentId=escrow.sellerAgentId,
                    amountAtomic=escrow.amountAtomic,
                    transactionId="circle-tx-1",
                    transactionHash="0xrealtransfer",
                    transactionState="INITIATED",
                    mode="circle",
                    toolResult={"transactionHash": "0xrealtransfer"},
                    createdAt=current,
                    updatedAt=current,
                )

        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        escrow = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
            },
        ).json()["escrow"]

        with patch.object(
            main, "get_ledger_settlement_client", return_value=FakeSettlementClient()
        ):
            response = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["settlementRecord"]["transactionHash"], "0xrealtransfer")
        state = self.client.get("/ledger/state").json()
        self.assertEqual(len(state["settlementRecords"]), 1)
        self.assertEqual(state["settlementRecords"][0]["status"], "submitted")

    def test_agent_transfer_calls_circle_then_moves_available_balance(self) -> None:
        class FakeSettlementClient:
            def __init__(self) -> None:
                self.calls = []

            async def submit_agent_transfer(
                self,
                *,
                from_agent_id,
                to_agent_id,
                amount_atomic,
                ref_id,
            ):
                self.calls.append(
                    {
                        "fromAgentId": from_agent_id,
                        "toAgentId": to_agent_id,
                        "amountAtomic": amount_atomic,
                        "refId": ref_id,
                    }
                )
                current = main.now_iso()
                return main.LedgerSettlementRecord(
                    recordId="settle_direct",
                    eventType="agent_transfer",
                    status="submitted",
                    chainMcpUrl="http://circle.test/mcp/",
                    transferId=ref_id,
                    fromAgentId=from_agent_id,
                    toAgentId=to_agent_id,
                    amountAtomic=amount_atomic,
                    transactionId="circle-transfer-1",
                    transactionHash="0xagenttransfer",
                    transactionState="INITIATED",
                    mode="circle",
                    toolResult={"transactionHash": "0xagenttransfer"},
                    createdAt=current,
                    updatedAt=current,
                )

        self.client.post(
            "/ledger/accounts/agent_sender/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_sender",
            agent_name="Sender",
            email="sender@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle_sender",
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_receiver",
            agent_name="Receiver",
            email="receiver@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle_receiver",
        )
        fake_settlement = FakeSettlementClient()

        with patch.object(
            main, "get_ledger_settlement_client", return_value=fake_settlement
        ):
            response = self.client.post(
                "/ledger/transfers",
                json={
                    "fromEmail": "sender@example.com",
                    "toEmail": "receiver@example.com",
                    "amountAtomic": "1250000",
                    "reason": "direct payment",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["settlementRecord"]["transactionHash"], "0xagenttransfer")
        self.assertEqual(fake_settlement.calls[0]["fromAgentId"], "agent_sender")
        self.assertEqual(fake_settlement.calls[0]["toAgentId"], "agent_receiver")
        accounts = {
            item.agentId: item for item in main.get_store().load().accounts
        }
        self.assertEqual(accounts["agent_sender"].availableAtomic, "3750000")
        self.assertEqual(accounts["agent_sender"].lockedAtomic, "0")
        self.assertEqual(accounts["agent_receiver"].availableAtomic, "1250000")
        self.assertEqual(accounts["agent_receiver"].lockedAtomic, "0")
        self.assertEqual([entry["entryType"] for entry in payload["entries"]], ["agent_transfer", "agent_transfer"])

    def test_agent_transfer_failure_does_not_mutate_ledger_balance(self) -> None:
        class FakeSettlementClient:
            async def submit_agent_transfer(self, **_kwargs):
                current = main.now_iso()
                record = main.LedgerSettlementRecord(
                    recordId="settle_failed_direct",
                    eventType="agent_transfer",
                    status="failed",
                    chainMcpUrl="http://circle.test/mcp/",
                    transferId="transfer_failed",
                    fromAgentId="agent_sender",
                    toAgentId="agent_receiver",
                    amountAtomic="1250000",
                    error="Circle transfer failed",
                    createdAt=current,
                    updatedAt=current,
                )
                raise main.LedgerSettlementError(record)

        self.client.post(
            "/ledger/accounts/agent_sender/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_sender",
            agent_name="Sender",
            email="sender@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle_sender",
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_receiver",
            agent_name="Receiver",
            email="receiver@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle_receiver",
        )

        with patch.object(
            main, "get_ledger_settlement_client", return_value=FakeSettlementClient()
        ):
            response = self.client.post(
                "/ledger/transfers",
                json={
                    "fromEmail": "sender@example.com",
                    "toEmail": "receiver@example.com",
                    "amountAtomic": "1250000",
                },
            )

        self.assertEqual(response.status_code, 502)
        accounts = {
            item.agentId: item for item in main.get_store().load().accounts
        }
        self.assertEqual(accounts["agent_sender"].availableAtomic, "5000000")
        self.assertEqual(accounts["agent_receiver"].availableAtomic, "0")

    def test_agent_transfer_requires_real_circle_settlement_enabled(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_sender/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_sender",
            agent_name="Sender",
            email="sender@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle_sender",
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_receiver",
            agent_name="Receiver",
            email="receiver@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle_receiver",
        )

        response = self.client.post(
            "/ledger/transfers",
            json={
                "fromEmail": "sender@example.com",
                "toEmail": "receiver@example.com",
                "amountAtomic": "1250000",
            },
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["detail"]["message"],
            "Circle settlement is required for direct agent transfers",
        )
        accounts = {
            item.agentId: item for item in main.get_store().load().accounts
        }
        self.assertEqual(accounts["agent_sender"].availableAtomic, "5000000")
        self.assertEqual(accounts["agent_receiver"].availableAtomic, "0")

    def test_settlement_client_uses_dedicated_circle_mcp_url(self) -> None:
        os.environ["LEDGER_SETTLEMENT_ENABLED"] = "true"
        os.environ["LEDGER_SETTLEMENT_MCP_URL"] = "http://circle.test/mcp/"
        main.get_ledger_settlement_client.cache_clear()

        client = main.get_ledger_settlement_client()

        self.assertTrue(client.enabled)
        self.assertEqual(client.settlement_mcp_url, "http://circle.test/mcp/")

    def test_required_settlement_failure_blocks_release(self) -> None:
        class FakeSettlementClient:
            enabled = True
            require_success = True

            async def submit_release(self, escrow):
                current = main.now_iso()
                record = main.LedgerSettlementRecord(
                    recordId="settle_failed",
                    eventType="escrow_release",
                    status="failed",
                    chainMcpUrl="http://chain.test/mcp/",
                    escrowId=escrow.escrowId,
                    fromAgentId=escrow.buyerAgentId,
                    toAgentId=escrow.sellerAgentId,
                    amountAtomic=escrow.amountAtomic,
                    error="Circle resource not found",
                    createdAt=current,
                    updatedAt=current,
                )
                raise main.LedgerSettlementError(record)

        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        escrow = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
            },
        ).json()["escrow"]

        with patch.object(
            main, "get_ledger_settlement_client", return_value=FakeSettlementClient()
        ):
            response = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release")

        self.assertEqual(response.status_code, 502)
        state = self.client.get("/ledger/state").json()
        escrow_state = state["escrows"][0]
        self.assertEqual(escrow_state["status"], "locked")

    def test_refund_escrow_moves_locked_funds_back_to_buyer_available(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        escrow = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
            },
        ).json()["escrow"]

        response = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/refund")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["escrow"]["status"], "refunded")
        accounts = {
            item["agentId"]: item for item in self.client.get("/ledger/state").json()["accounts"]
        }
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "5000000")
        self.assertEqual(accounts["agent_buyer"]["lockedAtomic"], "0")
        self.assertNotIn("agent_seller", accounts)

    def test_settled_escrow_cannot_be_mutated_again(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        escrow = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
            },
        ).json()["escrow"]
        self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release")

        refund = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/refund")
        release = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release")

        self.assertEqual(refund.status_code, 400)
        self.assertEqual(refund.json()["detail"], "escrow is not locked")
        self.assertEqual(release.status_code, 400)
        self.assertEqual(release.json()["detail"], "escrow is not locked")

    def test_rejects_non_integer_amounts(self) -> None:
        response = self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "1.5", "reason": "bad funding"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "amountAtomic must be a positive integer string",
        )

    def test_missing_escrow_returns_not_found(self) -> None:
        response = self.client.post("/ledger/escrows/escrow_missing/release")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "escrow not found")

    def test_state_persists_to_json_file(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        main.get_store.cache_clear()
        reloaded_client = TestClient(main.app)

        state = reloaded_client.get("/ledger/state").json()

        self.assertEqual(state["accounts"][0]["agentId"], "agent_buyer")
        self.assertEqual(state["accounts"][0]["availableAtomic"], "5000000")
        self.assertTrue(Path(self.state_path).exists())

    def test_route_payment_intent_tool_is_served_by_ledger(self) -> None:
        tool = getattr(main, "route_payment_intent_tool", None)
        self.assertIsNotNone(tool)

        result = asyncio.run(
            tool(
                purpose="paid api",
                deliveryMode="immediate_api",
                requiresAcceptance=False,
                externalService=True,
                serviceUrl="https://seller.example/x402",
            )
        )

        self.assertEqual(result["method"], "x402")
        self.assertEqual(result["allowedTools"], ["chain_x402_fetch"])

    def test_route_payment_intent_supports_funding_onramp(self) -> None:
        tool = getattr(main, "route_payment_intent_tool", None)
        self.assertIsNotNone(tool)

        result = asyncio.run(
            tool(
                purpose="fund agent wallet",
                deliveryMode="funding",
                requiresAcceptance=False,
                externalService=True,
            )
        )

        self.assertEqual(result["method"], "onramp")
        self.assertEqual(result["allowedTools"], ["agent_wallet_create_onramp_session"])

    def test_route_payment_intent_supports_direct_agent_transfer(self) -> None:
        tool = getattr(main, "route_payment_intent_tool", None)
        self.assertIsNotNone(tool)

        result = asyncio.run(
            tool(
                purpose="pay another agent now",
                deliveryMode="agent_transfer",
                requiresAcceptance=False,
                externalService=False,
            )
        )

        self.assertEqual(result["method"], "ledger_transfer")
        self.assertEqual(result["allowedTools"], ["agent_wallet_transfer"])

    def test_ledger_mcp_tools_operate_on_local_store(self) -> None:
        state_tool = getattr(main, "agent_wallet_get_ledger_state_tool", None)
        transfer_tool = getattr(main, "agent_wallet_transfer_tool", None)
        create_tool = getattr(main, "agent_wallet_create_escrow_tool", None)
        release_tool = getattr(main, "agent_wallet_release_escrow_tool", None)
        refund_tool = getattr(main, "agent_wallet_refund_escrow_tool", None)
        self.assertFalse(hasattr(main, "agent_wallet_credit_balance_tool"))
        self.assertIsNotNone(state_tool)
        self.assertIsNotNone(transfer_tool)
        self.assertIsNotNone(create_tool)
        self.assertIsNotNone(release_tool)
        self.assertIsNotNone(refund_tool)

        main.get_store().credit(
            agent_id="agent_buyer",
            amount_atomic="5000000",
            reason="demo funding",
            metadata={},
        )
        escrow = asyncio.run(
            create_tool(
                buyerAgentId="agent_buyer",
                sellerAgentId="agent_seller",
                amountAtomic="3000000",
                taskId="task_123",
                description="Research task",
            )
        )["escrow"]
        released = asyncio.run(release_tool(escrow["escrowId"]))["escrow"]
        state = asyncio.run(state_tool())

        self.assertEqual(released["status"], "released")
        accounts = {item["agentId"]: item for item in state["accounts"]}
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "2000000")
        self.assertEqual(accounts["agent_buyer"]["lockedAtomic"], "0")
        self.assertEqual(accounts["agent_seller"]["availableAtomic"], "3000000")

        main.get_store().credit(
            agent_id="agent_refund_buyer",
            amount_atomic="4000000",
            reason="demo funding",
            metadata={},
        )
        refund_escrow = asyncio.run(
            create_tool(
                buyerAgentId="agent_refund_buyer",
                sellerAgentId="agent_refund_seller",
                amountAtomic="1000000",
            )
        )["escrow"]
        refunded = asyncio.run(refund_tool(refund_escrow["escrowId"]))["escrow"]

        self.assertEqual(refunded["status"], "refunded")

    def test_onramp_mcp_tool_creates_session(self) -> None:
        os.environ["COINBASE_ONRAMP_MOCK"] = "true"
        main.get_coinbase_onramp_client.cache_clear()
        tool = getattr(main, "agent_wallet_create_onramp_session_tool", None)
        self.assertIsNotNone(tool)

        result = asyncio.run(
            tool(
                agentId="agentA",
                destinationAddress="0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
                paymentAmount="10.00",
                idempotencyKey="fund-agentA-10",
            )
        )

        self.assertEqual(result["agentId"], "agentA")
        self.assertEqual(result["status"], "created")
        self.assertIn("onrampUrl", result)


if __name__ == "__main__":
    unittest.main()
