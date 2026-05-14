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
        os.environ["LEDGER_STATE_PATH"] = self.state_path
        os.environ["LEDGER_CHAIN_RECORD_ENABLED"] = "false"
        os.environ["LEDGER_CHAIN_RECORD_REQUIRE_SUCCESS"] = "false"
        os.environ["LEDGER_SETTLEMENT_ENABLED"] = "false"
        os.environ["LEDGER_SETTLEMENT_REQUIRE_SUCCESS"] = "false"
        main.get_store.cache_clear()
        main.get_coinbase_onramp_client.cache_clear()
        main.get_chain_recorder.cache_clear()
        main.get_ledger_settlement_client.cache_clear()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.get_store.cache_clear()
        main.get_ledger_settlement_client.cache_clear()
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
        main.get_coinbase_onramp_client.cache_clear()
        main.get_chain_recorder.cache_clear()
        main.get_ledger_settlement_client.cache_clear()

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

    def test_root_serves_ledger_management_page(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("OntologyAgent Ledger", html)
        self.assertIn('id="ledger-state"', html)
        self.assertIn('id="credit-form"', html)
        self.assertIn('id="onramp-form"', html)
        self.assertIn('id="onramp-confirm-form"', html)
        self.assertIn('id="escrow-form"', html)
        self.assertIn('id="settlement-form"', html)
        self.assertIn("/ledger/state", html)
        self.assertIn("/onramp/sessions", html)
        self.assertIn("/ledger/accounts/", html)
        self.assertIn("/ledger/escrows", html)

    def test_management_page_helpers_render_and_call_ledger_api(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node is required to execute ledger page helpers")

        response = self.client.get("/")

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
                "id, textContent: '', value: '', disabled: false, children: [], style: {},"
                "appendChild(child) { this.children.push(child); if (!this.value && child.value) this.value = child.value; },"
                "replaceChildren() { this.children = []; this.value = ''; },"
                "addEventListener(event, handler) { listeners.set(`${id}:${event}`, handler.name || 'anonymous'); },"
                "focus() {}"
                "});"
                "global.document = {"
                "getElementById(id) { if (!elements.has(id)) elements.set(id, makeElement(id)); return elements.get(id); },"
                "querySelectorAll() { return []; },"
                "createElement(tag) { return makeElement(tag); }"
                "};"
                "global.fetch = async (url, options = {}) => {"
                "fetchCalls.push({ url, method: options.method || 'GET', body: options.body || null });"
                "if (url === '/ledger/state') return { ok: true, json: async () => ({"
                "accounts: [{ agentId: 'agent_buyer', availableAtomic: '5000000', lockedAtomic: '3000000' }],"
                "entries: [{ entryId: 'entry_1', entryType: 'credit', agentId: 'agent_buyer' }],"
                "escrows: [{ escrowId: 'escrow_1', buyerAgentId: 'agent_buyer', sellerAgentId: 'agent_seller', amountAtomic: '3000000', status: 'locked' }],"
                "onrampSessions: [{ sessionId: 'onramp_1', agentId: 'agentA', paymentAmount: '10.00', status: 'created', onrampUrl: 'https://pay.coinbase.com/buy/select-asset?sessionToken=abc' }]"
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
                "elements.get('credit-agent-id').value = 'agent_buyer';"
                "elements.get('credit-amount').value = '5000000';"
                "elements.get('credit-reason').value = 'demo funding';"
                "await creditAccount({ preventDefault() {} });"
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
                "stateText: elements.get('ledger-state').textContent,"
                "escrowSelection: selectedEscrowId(),"
                "onrampSelection: selectedOnrampSessionId(),"
                "creditListener: listeners.get('credit-form:submit'),"
                "onrampListener: listeners.get('onramp-form:submit'),"
                "onrampConfirmListener: listeners.get('onramp-confirm-form:submit'),"
                "escrowListener: listeners.get('escrow-form:submit'),"
                "settlementListener: listeners.get('settlement-form:submit'),"
                "creditCall: fetchCalls.find((call) => call.url === '/ledger/accounts/agent_buyer/credit'),"
                "onrampCall: fetchCalls.find((call) => call.url === '/onramp/sessions'),"
                "confirmCall: fetchCalls.find((call) => call.url === '/onramp/sessions/onramp_1/confirm'),"
                "openCall: window.openCalls[0],"
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
        self.assertIn("Accounts: 1", output["stateText"])
        self.assertIn("Escrows: 1", output["stateText"])
        self.assertIn("Onramps: 1", output["stateText"])
        self.assertEqual(output["escrowSelection"], "escrow_1")
        self.assertEqual(output["onrampSelection"], "onramp_1")
        self.assertEqual(output["creditListener"], "creditAccount")
        self.assertEqual(output["onrampListener"], "createOnrampSession")
        self.assertEqual(output["onrampConfirmListener"], "confirmOnrampSession")
        self.assertEqual(output["escrowListener"], "createEscrow")
        self.assertEqual(output["settlementListener"], "preventSettlementSubmit")
        self.assertEqual(output["creditCall"]["method"], "POST")
        self.assertEqual(
            json.loads(output["creditCall"]["body"]),
            {"amountAtomic": "5000000", "reason": "demo funding"},
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

    def test_ledger_mcp_tools_operate_on_local_store(self) -> None:
        credit_tool = getattr(main, "agent_wallet_credit_balance_tool", None)
        state_tool = getattr(main, "agent_wallet_get_ledger_state_tool", None)
        create_tool = getattr(main, "agent_wallet_create_escrow_tool", None)
        release_tool = getattr(main, "agent_wallet_release_escrow_tool", None)
        refund_tool = getattr(main, "agent_wallet_refund_escrow_tool", None)
        self.assertIsNotNone(credit_tool)
        self.assertIsNotNone(state_tool)
        self.assertIsNotNone(create_tool)
        self.assertIsNotNone(release_tool)
        self.assertIsNotNone(refund_tool)

        asyncio.run(
            credit_tool(
                agentId="agent_buyer",
                amountAtomic="5000000",
                reason="demo funding",
            )
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

        asyncio.run(
            credit_tool(
                agentId="agent_refund_buyer",
                amountAtomic="4000000",
                reason="demo funding",
            )
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
