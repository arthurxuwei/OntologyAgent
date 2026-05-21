import asyncio
import base64
import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import jwt
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
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
        self.previous_settlement_http_url = os.environ.get("LEDGER_SETTLEMENT_HTTP_URL")
        self.previous_wallet_http_url = os.environ.get("LEDGER_WALLET_HTTP_URL")
        self.previous_circle_api_key = os.environ.get("CIRCLE_API_KEY")
        self.previous_circle_webhook_verify = os.environ.get("CIRCLE_WEBHOOK_VERIFY_SIGNATURE")
        os.environ["LEDGER_STATE_PATH"] = self.state_path
        os.environ["LEDGER_CHAIN_RECORD_ENABLED"] = "false"
        os.environ["LEDGER_CHAIN_RECORD_REQUIRE_SUCCESS"] = "false"
        os.environ["LEDGER_SETTLEMENT_ENABLED"] = "false"
        os.environ["LEDGER_SETTLEMENT_REQUIRE_SUCCESS"] = "false"
        os.environ["CIRCLE_WEBHOOK_VERIFY_SIGNATURE"] = "false"
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
        self._restore_env("LEDGER_SETTLEMENT_HTTP_URL", self.previous_settlement_http_url)
        self._restore_env("LEDGER_WALLET_HTTP_URL", self.previous_wallet_http_url)
        self._restore_env("CIRCLE_API_KEY", self.previous_circle_api_key)
        self._restore_env("CIRCLE_WEBHOOK_VERIFY_SIGNATURE", self.previous_circle_webhook_verify)
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

    def test_ledger_state_loads_legacy_record_result_fields(self) -> None:
        now = main.now_iso()
        legacy_transport_url_key = "chain" + "M" + "cpUrl"
        legacy_state = {
            "accounts": [],
            "entries": [],
            "escrows": [],
            "onrampSessions": [],
            "onrampEvents": [],
            "legacySummary": {"ignored": True},
            "chainRecords": [
                {
                    "recordId": "chain_legacy",
                    "eventType": "credit",
                    "status": "submitted",
                    "chainTool": "chain_submit_execution",
                    legacy_transport_url_key: "http://chain.test/legacy/",
                    "recorderAddress": "0x000000000000000000000000000000000000dEaD",
                    "toolResult": {"txHash": "0xchain"},
                    "legacyExtra": "ignored",
                    "createdAt": now,
                    "updatedAt": now,
                },
                {
                    "recordId": "chain_missing_url",
                    "eventType": "credit",
                    "status": "failed",
                    "recorderAddress": "0x000000000000000000000000000000000000dEaD",
                    "createdAt": now,
                    "updatedAt": now,
                }
            ],
            "settlementRecords": [
                {
                    "recordId": "settle_legacy",
                    "eventType": "withdrawal",
                    "status": "submitted",
                    "settlementTool": "agent_wallet_withdraw",
                    legacy_transport_url_key: "http://circle.test/legacy/",
                    "fromAgentId": "agent_sender",
                    "amountAtomic": "1000000",
                    "toolResult": {"transactionHash": "0xsettle"},
                    "legacyExtra": "ignored",
                    "createdAt": now,
                    "updatedAt": now,
                },
                {
                    "recordId": "settle_missing_url",
                    "eventType": "withdrawal",
                    "status": "failed",
                    "fromAgentId": "agent_sender",
                    "amountAtomic": "1000000",
                    "createdAt": now,
                    "updatedAt": now,
                }
            ],
        }
        Path(self.state_path).write_text(json.dumps(legacy_state), encoding="utf-8")

        state = main.get_store().load()

        self.assertEqual(state.chainRecords[0].chainHttpUrl, "http://chain.test/legacy/")
        self.assertEqual(state.chainRecords[1].chainHttpUrl, main.DEFAULT_CHAIN_HTTP_URL)
        self.assertEqual(state.chainRecords[0].actionResult, {"txHash": "0xchain"})
        self.assertEqual(
            state.settlementRecords[0].settlementHttpUrl,
            "http://circle.test/legacy/",
        )
        self.assertEqual(
            state.settlementRecords[1].settlementHttpUrl,
            main.DEFAULT_SETTLEMENT_HTTP_URL,
        )
        self.assertEqual(
            state.settlementRecords[0].actionResult,
            {"transactionHash": "0xsettle"},
        )

    def test_route_payment_intent_is_served_by_rest(self) -> None:
        response = self.client.post(
            "/ledger/payment/route",
            json={
                "purpose": "buy async service",
                "deliveryMode": "async_task",
                "requiresAcceptance": True,
                "externalService": False,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["method"], "ledger_escrow")
        self.assertEqual(
            payload["allowedTools"],
            [
                "agent_wallet_create_escrow",
                "agent_wallet_release_escrow",
                "agent_wallet_refund_escrow",
            ],
        )

    def test_chain_recorder_posts_rest_execution(self) -> None:
        calls = []

        async def handler(request):
            calls.append(
                (
                    request.url.path,
                    request.headers.get("content-type"),
                    json.loads(request.content.decode("utf-8")),
                )
            )
            return httpx.Response(
                200,
                json={
                    "execution": {"txHash": "0xabc123", "mode": "mock"},
                    "settlement": {"kind": "submitted"},
                },
            )

        recorder = main.LedgerChainRecorder(
            enabled=True,
            chain_http_url="http://chain.test",
            recorder_address="0x000000000000000000000000000000000000dEaD",
            timeout_seconds=30,
            max_payload_bytes=2048,
            require_success=True,
            transport=httpx.MockTransport(handler),
        )

        entry = main.LedgerEntry(
            entryId="entry_1",
            entryType="credit",
            agentId="agentA",
            availableDeltaAtomic="100",
            createdAt=main.now_iso(),
        )
        record = asyncio.run(
            recorder.submit(
                event_type="credit",
                escrow=None,
                entries=[entry],
                payload={"eventType": "credit"},
            )
        )

        self.assertEqual(calls[0][0], "/chain/executions")
        self.assertEqual(calls[0][2]["to"], "0x000000000000000000000000000000000000dEaD")
        self.assertEqual(record.status, "submitted")
        self.assertEqual(record.txHash, "0xabc123")

    def test_wallet_client_uses_circle_rest_status(self) -> None:
        async def handler(request):
            self.assertEqual(request.url.path, "/circle/wallets/status")
            self.assertEqual(request.url.params["walletAddress"], "0xabc")
            return httpx.Response(200, json={"balances": {"USDC": "1.23"}})

        client = main.LedgerWalletClient(
            wallet_http_url="http://circle.test",
            timeout_seconds=30,
            transport=httpx.MockTransport(handler),
        )

        status = asyncio.run(client.status(wallet_address="0xabc", circle_wallet_id=None))

        self.assertEqual(status["balances"]["USDC"], "1.23")

    def test_auth_session_returns_anonymous_without_cookie(self) -> None:
        response = self.client.get("/auth/session")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"authenticated": False, "user": None})

    def test_github_login_redirects_to_github_oauth(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GITHUB_CLIENT_ID": "github-client",
                "GITHUB_CLIENT_SECRET": "github-secret",
                "AUTH_SESSION_SECRET": "session-secret",
                "PUBLIC_BASE_URL": "https://ledger.example.test",
            },
        ):
            response = self.client.get("/auth/github/login", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        location = response.headers["location"]
        self.assertTrue(location.startswith("https://github.com/login/oauth/authorize?"))
        self.assertIn("client_id=github-client", location)
        self.assertNotIn("redirect_uri=", location)
        self.assertIn("scope=read%3Auser+user%3Aemail", location)
        self.assertIn("chief_ledger_oauth_state=", response.headers["set-cookie"])

    def test_github_login_accepts_dashboard_return_path(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GITHUB_CLIENT_ID": "github-client",
                "GITHUB_CLIENT_SECRET": "github-secret",
                "AUTH_SESSION_SECRET": "session-secret",
                "PUBLIC_BASE_URL": "https://ledger.example.test",
            },
        ):
            response = self.client.get(
                "/auth/github/login?returnTo=/dashboard%3FclaimCode%3Dclm_abc%26agentId%3Dagent_1",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 307)
        set_cookie = response.headers["set-cookie"]
        self.assertIn("chief_ledger_oauth_return=", set_cookie)
        self.assertIn("/dashboard%3FclaimCode%3Dclm_abc%26agentId%3Dagent_1", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=lax", set_cookie)
        self.assertIn("Max-Age=600", set_cookie)
        self.assertIn("Secure", set_cookie)

    def test_github_login_rejects_invalid_dashboard_return_paths(self) -> None:
        invalid_return_paths = (
            "/dashboard.evil?claimCode=clm_bad",
            "/dashboard/../admin?claimCode=clm_bad",
            "//evil.test/dashboard?claimCode=clm_bad",
            "https://evil.test/dashboard?claimCode=clm_bad",
        )

        for return_path in invalid_return_paths:
            with self.subTest(return_path=return_path), patch.dict(
                os.environ,
                {
                    "GITHUB_CLIENT_ID": "github-client",
                    "GITHUB_CLIENT_SECRET": "github-secret",
                    "AUTH_SESSION_SECRET": "session-secret",
                    "PUBLIC_BASE_URL": "https://ledger.example.test",
                },
            ):
                response = self.client.get(
                    f"/auth/github/login?returnTo={return_path}",
                    follow_redirects=False,
                )

            self.assertEqual(response.status_code, 307)
            set_cookie = response.headers["set-cookie"]
            self.assertIn("chief_ledger_oauth_return=", set_cookie)
            self.assertIn("/dashboard", set_cookie)
            self.assertNotIn("clm_bad", set_cookie)

    def test_github_callback_redirects_to_stored_claim_return_path(self) -> None:
        async def fake_fetch_github_user(_code, redirect_uri=None):
            return {
                "provider": "github",
                "login": "octo",
                "name": "Octo User",
                "email": "owner@example.com",
                "avatar_url": None,
            }

        with patch.dict(os.environ, {"AUTH_SESSION_SECRET": "session-secret"}), patch.object(
            main,
            "fetch_github_user",
            side_effect=fake_fetch_github_user,
        ):
            response = self.client.get(
                "/auth/github/callback?code=abc&state=oauth-state",
                headers={
                    "Cookie": (
                        "chief_ledger_oauth_state=oauth-state; "
                        "chief_ledger_oauth_return=/dashboard%3FclaimCode%3Dclm_abc%26agentId%3Dagent_1"
                    )
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response.headers["location"],
            "/dashboard?claimCode=clm_abc&agentId=agent_1",
        )
        self.assertIn("chief_ledger_oauth_return=", response.headers["set-cookie"])

    def test_github_callback_rejects_invalid_stored_return_paths(self) -> None:
        async def fake_fetch_github_user(_code, redirect_uri=None):
            return {
                "provider": "github",
                "login": "octo",
                "name": "Octo User",
                "email": "owner@example.com",
                "avatar_url": None,
            }

        invalid_return_paths = (
            "/dashboard.evil?claimCode=clm_bad",
            "/dashboard/../admin?claimCode=clm_bad",
            "//evil.test/dashboard?claimCode=clm_bad",
            "https://evil.test/dashboard?claimCode=clm_bad",
        )

        for return_path in invalid_return_paths:
            with self.subTest(return_path=return_path), patch.dict(
                os.environ,
                {"AUTH_SESSION_SECRET": "session-secret"},
            ), patch.object(
                main,
                "fetch_github_user",
                side_effect=fake_fetch_github_user,
            ):
                response = self.client.get(
                    "/auth/github/callback?code=abc&state=oauth-state",
                    headers={
                        "Cookie": (
                            "chief_ledger_oauth_state=oauth-state; "
                            f"chief_ledger_oauth_return={return_path}"
                        )
                    },
                    follow_redirects=False,
                )

            self.assertEqual(response.status_code, 307)
            self.assertEqual(response.headers["location"], "/dashboard")

    def test_github_callback_error_preserves_stored_claim_return_and_clears_oauth_cookies(self) -> None:
        response = self.client.get(
            "/auth/github/callback?error=bad state&state=oauth-state",
            headers={
                "Cookie": (
                    "chief_ledger_oauth_state=oauth-state; "
                    "chief_ledger_oauth_return=/dashboard%3FclaimCode%3Dclm_abc%26agentId%3Dagent_1"
                )
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response.headers["location"],
            "/dashboard?claimCode=clm_abc&agentId=agent_1&auth_error=bad+state",
        )
        set_cookie = response.headers["set-cookie"]
        self.assertIn("chief_ledger_oauth_state=", set_cookie)
        self.assertIn("chief_ledger_oauth_return=", set_cookie)
        self.assertIn("Max-Age=0", set_cookie)

    def test_root_can_receive_github_oauth_callback_error(self) -> None:
        response = self.client.get("/?error=access_denied", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/dashboard?auth_error=access_denied")

    def test_dashboard_can_receive_github_oauth_callback_error(self) -> None:
        response = self.client.get("/dashboard?error=access_denied", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/dashboard?auth_error=access_denied")

    def test_nested_dashboard_github_callback_receives_oauth_error(self) -> None:
        response = self.client.get(
            "/dashboard/auth/github/callback?error=access_denied",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/dashboard?auth_error=access_denied")

    def test_github_login_requires_oauth_config(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.get("/auth/github/login", follow_redirects=False)

        self.assertEqual(response.status_code, 500)
        self.assertIn("GITHUB_CLIENT_ID", response.json()["detail"])

    def test_auth_session_returns_signed_github_user(self) -> None:
        with patch.dict(os.environ, {"AUTH_SESSION_SECRET": "session-secret"}):
            session = main.sign_auth_session(
                {
                    "provider": "github",
                    "login": "octo",
                    "name": "Octo User",
                    "email": "OWNER@EXAMPLE.COM",
                    "avatar_url": "https://example.test/avatar.png",
                }
            )
            response = self.client.get(
                "/auth/session",
                headers={"Cookie": f"chief_ledger_session={session}"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["user"]["provider"], "github")
        self.assertEqual(payload["user"]["login"], "octo")
        self.assertEqual(payload["user"]["email"], "owner@example.com")

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

    def test_legacy_protocol_endpoint_is_not_served(self) -> None:
        with TestClient(main.app) as client:
            response = client.get("/" + "mc" + "p/")

        self.assertEqual(response.status_code, 404)

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
        self.assertNotIn('id="onramp-form"', html)
        self.assertNotIn('id="onramp-confirm-form"', html)
        self.assertNotIn("Coinbase Onramp", html)
        self.assertNotIn("Confirm Onramp", html)
        self.assertNotIn('id="escrow-form"', html)
        self.assertNotIn("Create Escrow", html)
        self.assertIn('id="settlement-form"', html)
        self.assertIn("/admin/ledger/state", html)
        self.assertNotIn("/onramp/sessions", html)
        self.assertIn("/ledger/escrows", html)
        self.assertIn("Onramp Sessions", html)
        self.assertIn('{ label: "Email"', html)
        self.assertIn("Gateway USDC Available", html)
        self.assertIn("Pending Deposits", html)
        self.assertIn('{ label: "Gateway Available"', html)
        self.assertIn('{ label: "Gateway Withdrawable"', html)
        self.assertIn('{ label: "Pending Deposits Atomic"', html)
        self.assertIn('{ label: "Pending Batch Atomic"', html)

    def test_dashboard_serves_user_dashboard_page(self) -> None:
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Agent Wallet · MVP Dashboard", html)
        self.assertIn('href="/auth/github/login"', html)
        self.assertIn("fetch('/auth/session'", html)
        self.assertIn("if (!registered) return <window.MvpGithubAuthScreen />", html)
        self.assertIn("if (!claimed)    return <window.MvpClaimScreen />", html)
        self.assertIn("window.ClaimForm = ClaimForm", html)
        self.assertIn("fetch(`/dashboard/claimable-agents?claimed=${claimed}`)", html)
        self.assertNotIn("email=${email}", html)
        self.assertIn("t('mvp.dash.claim.code_label')", html)
        self.assertIn("const canValidate = trimmedCode.length > 0", html)
        self.assertIn("candidate.claimCode", html)
        self.assertIn("{t('mvp.dash.claim.validate_button')} →", html)
        self.assertNotIn("DEMO CODE · paste any to try the flow", html)
        self.assertNotIn("function DemoHint", html)
        self.assertNotIn("demo_hint", html)
        self.assertIn("clm_…", html)
        self.assertIn("candidate.dashboard.agent.role", html)
        self.assertIn("const activeLabel = activeMeta && activeMeta.agent && activeMeta.agent.name", html)
        self.assertIn("{activeLabel}", html)
        self.assertNotIn("agent id / name", html)
        self.assertNotIn("{t('mvp.dash.claim.validate_button')} ->", html)
        self.assertNotIn("function ClaimStatusCard", html)
        self.assertNotIn("Looking up agents", html)
        self.assertNotIn("function CandidateButton", html)
        self.assertIn("window.localStorage.getItem(STORAGE_KEYS.mockState) || 'day1'", html)
        self.assertNotIn('type="email"', html)
        self.assertNotIn('placeholder="you@example.com"', html)
        self.assertNotIn("<window.MockStateToggle />", html)
        self.assertIn("fetch('/dashboard/data')", html)
        self.assertNotIn("const emailQuery = ownerEmail", html)
        self.assertIn("fetch('/onramp/sessions'", html)
        self.assertIn("fullWalletAddress", html)
        self.assertIn("mvp.dash.funding.add_description", html)
        self.assertIn("mvp.dash.funding.receive_eyebrow", html)
        self.assertIn("mvp.dash.funding.onramp_coming_soon", html)
        self.assertNotIn("<CardTitle>{t('mvp.dash.funding.onramp_title')}</CardTitle>", html)
        self.assertNotIn("setOnrampOpen(true)", html)
        self.assertIn("const qrAddress = String(address || '').trim();", html)
        self.assertIn("qr.addData(qrAddress);", html)
        self.assertIn("data-qr-address={qrAddress}", html)
        self.assertIn("jsQR.js", html)
        self.assertIn("fetch('/ledger/withdrawals'", html)
        self.assertIn("<window.AddressPicker", html)
        self.assertIn("<window.AddAddressModal", html)
        self.assertIn("wallets, defaultWalletId", html)
        self.assertIn("const amountAtomic = usdcToAtomic(amount);", html)
        self.assertIn("amountAtomic,", html)
        self.assertIn("ownerEmail", html)
        self.assertIn("const MIN_WITHDRAW = 1;", html)
        self.assertIn("Min 1 USDC", html)
        self.assertNotIn("claimAgent('agentA')", html)
        self.assertNotIn("mvp.dash.settings.danger_button", html)
        self.assertNotIn("mvp.dash.settings.open_demo", html)

    def test_dashboard_supports_claim_code_deep_link_auto_claim(self) -> None:
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("params.get('claimCode')", html)
        self.assertIn("params.get('agentId')", html)
        self.assertIn("returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}", html)
        self.assertIn("function DeepLinkClaimRunner()", html)
        self.assertIn("const consumedRef = React.useRef(false);", html)
        self.assertIn("authChecked, claimToken, deepLinkAgentId, currentUser,", html)
        self.assertIn("!authChecked || consumedRef.current", html)
        self.assertIn("fetch(`/dashboard/claimable-agents?claimed=${claimed}`)", html)
        self.assertIn("const normalizedDeepLinkAgentId = deepLinkAgentId.trim();", html)
        self.assertIn("String(candidate.agentId || '').trim() === normalizedDeepLinkAgentId", html)
        self.assertIn("window.history.replaceState({}, '', cleanUrl.toString())", html)
        self.assertIn("<DeepLinkClaimRunner />", html)
        self.assertIn("<DashboardRouter />", html)
        self.assertLess(html.index("<DeepLinkClaimRunner />"), html.index("<DashboardRouter />"))

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
        store.transfer_between_agents(
            from_agent_id="agent_alpha",
            to_agent_id="agent_beta",
            amount_atomic="250000",
            reason="remark should not render",
            metadata={
                "fromEmail": "owner@example.com",
                "toEmail": "other@example.com",
            },
            transfer_id="transfer_123",
            settlement_record_id=None,
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
        self.assertEqual(
            alpha["agent"]["fullWalletAddress"],
            "0x1111111111111111111111111111111111111111",
        )
        self.assertEqual(alpha["balance"]["available"], 2.0)
        self.assertEqual(alpha["balance"]["locked"], 0.5)
        self.assertEqual(alpha["balance"]["lifetimeIn"], 2.5)
        self.assertEqual(alpha["balance"]["lifetimeOut"], 0.75)
        self.assertEqual(alpha["transactions"][0]["counterparty"], "other@example.com")
        self.assertNotEqual(alpha["transactions"][0]["counterparty"], "agent_beta")
        self.assertNotEqual(alpha["transactions"][0]["counterparty"], "remark should not render")
        self.assertEqual(alpha["transactions"][0]["status"], "released")

    def test_dashboard_transaction_exposes_pending_settlement_and_gas_metadata(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="receiver",
            agent_name="Receiver Agent",
            email="receiver@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-receiver",
        )
        store.record_dashboard_event(
            entry_type="pending_settlement",
            agent_id="receiver",
            reason="nanopayment pending",
            metadata={
                "dashboardStatus": "pending_settle",
                "amountAtomic": "1000",
                "counterpartyEmail": "payer@example.com",
            },
        )
        store.record_dashboard_event(
            entry_type="withdrawal_submitted",
            agent_id="receiver",
            reason="withdrawal submitted",
            metadata={
                "dashboardStatus": "withdraw_submitted",
                "amountAtomic": "1000000",
                "gasFeeAtomic": "3000",
                "netAmountAtomic": "997000",
                "destinationAddress": "0x2222222222222222222222222222222222222222",
                "network": "Base",
                "txHash": "0xsubmitted",
            },
        )

        state = main.build_dashboard_data(
            main.get_store().load().model_dump(),
            owner_email="receiver@example.com",
        )

        data = state["agents"]["receiver"]
        self.assertEqual(data["balance"]["pendingSettlement"], 0.001)
        statuses = [tx["status"] for tx in data["transactions"]]
        self.assertIn("pending_settle", statuses)
        self.assertIn("withdraw_submitted", statuses)
        submitted = next(tx for tx in data["transactions"] if tx["status"] == "withdraw_submitted")
        self.assertEqual(submitted["gasFeeAtomic"], "3000")
        self.assertEqual(submitted["netAmountAtomic"], "997000")
        self.assertEqual(submitted["txHash"], "0xsubmitted")

    def test_dashboard_withdrawal_status_ignores_blank_or_non_string_dashboard_status(self) -> None:
        for dashboard_status in ("", "   ", None, False):
            with self.subTest(dashboard_status=dashboard_status):
                tx = main.dashboard_transaction(
                    {
                        "entryId": "entry_withdrawal",
                        "entryType": "withdrawal",
                        "agentId": "receiver",
                        "availableDeltaAtomic": "-1000000",
                        "metadata": {"dashboardStatus": dashboard_status},
                        "createdAt": main.now_iso(),
                    },
                    {},
                )

                self.assertEqual(tx["status"], "withdrawn")

    def test_wallet_webhook_records_pending_inbound_before_gateway_credit(self) -> None:
        class FakeWalletClient:
            async def gateway_deposit(self, request):
                return {
                    "mode": "gateway_deposit",
                    "gatewayBalance": {"availableAtomic": request.amountAtomic},
                    "depositTransactionId": "deposit-tx",
                    "raw": {"provider": "secret"},
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_topup",
            agent_name="Topup Agent",
            email="topup@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-topup",
        )
        payload = {
            "notificationId": "notif_topup_1",
            "notificationType": "transactions.inbound",
            "notification": {
                "id": "circle-tx-1",
                "state": "COMPLETE",
                "transactionType": "INBOUND",
                "destinationAddress": "0x1111111111111111111111111111111111111111",
                "walletId": "circle-topup",
                "tokenSymbol": "USDC",
                "amount": "2.5",
            },
        }

        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            result = asyncio.run(main.process_circle_wallet_webhook(payload))
            duplicate = asyncio.run(main.process_circle_wallet_webhook(payload))

        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["account"]["availableAtomic"], "2500000")
        self.assertEqual(duplicate["status"], "duplicate")
        entries = main.get_store().load().entries
        self.assertEqual(len(entries), 2)
        self.assertEqual(
            [entry.metadata.get("dashboardStatus") for entry in entries],
            ["pending_inbound_chain", "credited"],
        )
        pending_entry, credited_entry = entries
        self.assertEqual(pending_entry.entryType, "pending_inbound")
        self.assertEqual(pending_entry.availableDeltaAtomic, "0")
        self.assertEqual(pending_entry.metadata["amountAtomic"], "2500000")
        self.assertEqual(pending_entry.metadata["notificationId"], "notif_topup_1")
        self.assertEqual(pending_entry.metadata["circleTransactionId"], "circle-tx-1")
        self.assertEqual(
            pending_entry.metadata["gatewayRefId"],
            "circle-webhook:notif_topup_1",
        )
        self.assertEqual(credited_entry.entryType, "credit")
        self.assertEqual(credited_entry.availableDeltaAtomic, "2500000")
        self.assertEqual(credited_entry.metadata["amountAtomic"], "2500000")
        self.assertEqual(credited_entry.metadata["notificationId"], "notif_topup_1")
        self.assertEqual(credited_entry.metadata["circleTransactionId"], "circle-tx-1")
        self.assertEqual(
            credited_entry.metadata["gatewayRefId"],
            "circle-webhook:notif_topup_1",
        )
        self.assertEqual(
            credited_entry.metadata["linkedEntryId"],
            pending_entry.entryId,
        )
        self.assertEqual(credited_entry.metadata["depositTransactionId"], "deposit-tx")
        self.assertEqual(
            credited_entry.metadata["gatewayBalance"],
            {"availableAtomic": "2500000"},
        )
        self.assertNotIn("gatewayDepositResult", credited_entry.metadata)
        self.assertNotIn("raw", credited_entry.metadata)

    def test_wallet_webhook_received_replay_completes_missing_entries(self) -> None:
        class FakeWalletClient:
            async def gateway_deposit(self, request):
                return {
                    "mode": "gateway_deposit",
                    "gatewayBalance": {"availableAtomic": request.amountAtomic},
                    "depositTransactionId": "deposit-recovered",
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_topup",
            agent_name="Topup Agent",
            email="topup@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-topup",
        )
        payload = {
            "notificationId": "notif_topup_received",
            "notificationType": "transactions.inbound",
            "notification": {
                "id": "circle-tx-received",
                "state": "COMPLETE",
                "transactionType": "INBOUND",
                "destinationAddress": "0x1111111111111111111111111111111111111111",
                "walletId": "circle-topup",
                "tokenSymbol": "USDC",
                "amount": "2.5",
            },
        }
        store.save_circle_webhook_event(
            main.circle_webhook_event_record(
                notification_id="notif_topup_received",
                notification_type="transactions.inbound",
                status="received",
                payload=payload,
                transaction_id="circle-tx-received",
                agent_id="agent_topup",
                wallet_address="0x1111111111111111111111111111111111111111",
                circle_wallet_id="circle-topup",
                amount_atomic="2500000",
                reason="gateway_deposit_started",
            )
        )

        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            result = asyncio.run(main.process_circle_wallet_webhook(payload))

        self.assertEqual(result["status"], "processed")
        entries = main.get_store().load().entries
        self.assertEqual(
            [entry.metadata.get("dashboardStatus") for entry in entries],
            ["pending_inbound_chain", "credited"],
        )
        self.assertEqual(entries[1].metadata["linkedEntryId"], entries[0].entryId)
        self.assertEqual(entries[1].metadata["depositTransactionId"], "deposit-recovered")
        self.assertEqual(result["account"]["availableAtomic"], "2500000")

    def test_wallet_webhook_processed_replay_completes_missing_credit(self) -> None:
        class FakeWalletClient:
            async def gateway_deposit(self, _request):
                raise AssertionError("processed webhook replay must not call gateway_deposit")

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_topup",
            agent_name="Topup Agent",
            email="topup@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-topup",
        )
        payload = {
            "notificationId": "notif_topup_processed",
            "notificationType": "transactions.inbound",
            "notification": {
                "id": "circle-tx-processed",
                "state": "COMPLETE",
                "transactionType": "INBOUND",
                "destinationAddress": "0x1111111111111111111111111111111111111111",
                "walletId": "circle-topup",
                "tokenSymbol": "USDC",
                "amount": "2.5",
            },
        }
        store.save_circle_webhook_event(
            main.circle_webhook_event_record(
                notification_id="notif_topup_processed",
                notification_type="transactions.inbound",
                status="processed",
                payload=payload,
                transaction_id="circle-tx-processed",
                agent_id="agent_topup",
                wallet_address="0x1111111111111111111111111111111111111111",
                circle_wallet_id="circle-topup",
                amount_atomic="2500000",
                reason="gateway_deposit_completed",
                gateway_deposit_result={
                    "mode": "gateway_deposit",
                    "gatewayBalance": {"availableAtomic": "2500000"},
                    "depositTransactionId": "deposit-processed",
                    "raw": {"provider": "secret"},
                },
            )
        )

        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            result = asyncio.run(main.process_circle_wallet_webhook(payload))
            duplicate = asyncio.run(main.process_circle_wallet_webhook(payload))

        self.assertEqual(result["status"], "processed")
        self.assertEqual(duplicate["status"], "duplicate")
        entries = main.get_store().load().entries
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].entryType, "pending_inbound")
        self.assertEqual(entries[1].entryType, "credit")
        self.assertEqual(entries[1].metadata["linkedEntryId"], entries[0].entryId)
        self.assertEqual(entries[1].metadata["depositTransactionId"], "deposit-processed")
        self.assertNotIn("raw", entries[1].metadata)
        self.assertNotIn("gatewayDepositResult", entries[1].metadata)
        self.assertEqual(result["account"]["availableAtomic"], "2500000")

    def test_dashboard_pending_settlement_balance_uses_escrow_amount_fallback(self) -> None:
        state = main.build_dashboard_data(
            {
                "accounts": [
                    {
                        "agentId": "receiver",
                        "agentName": "Receiver Agent",
                        "email": "receiver@example.com",
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "availableAtomic": "0",
                        "lockedAtomic": "0",
                    }
                ],
                "entries": [
                    {
                        "entryId": "entry_pending",
                        "entryType": "pending_settlement",
                        "agentId": "receiver",
                        "availableDeltaAtomic": "0",
                        "lockedDeltaAtomic": "0",
                        "escrowId": "escrow_pending",
                        "metadata": {},
                        "createdAt": main.now_iso(),
                    }
                ],
                "escrows": [
                    {
                        "escrowId": "escrow_pending",
                        "buyerAgentId": "payer",
                        "sellerAgentId": "receiver",
                        "amountAtomic": "500000",
                        "description": "pending task",
                    }
                ],
            },
            owner_email="receiver@example.com",
        )

        data = state["agents"]["receiver"]
        self.assertEqual(data["transactions"][0]["amountAtomic"], "500000")
        self.assertEqual(data["balance"]["pendingSettlement"], 0.5)
        self.assertEqual(data["balance"]["pendingSettlementAtomic"], "500000")

    def test_dashboard_claimable_agents_come_from_unclaimed_email_accounts(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_alpha",
            agent_name="Alpha Research",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-alpha",
            account_type="EOA",
        )
        store.bind_account_wallet(
            agent_id="agent_beta",
            agent_name="Beta Research",
            email="owner@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle-beta",
            account_type="EOA",
        )
        store.bind_account_wallet(
            agent_id="agent_other",
            agent_name="Other Research",
            email="other@example.com",
            wallet_address="0x3333333333333333333333333333333333333333",
            circle_wallet_id="circle-other",
            account_type="EOA",
        )
        store.bind_account_wallet(
            agent_id="agent_sca",
            agent_name="SCA Research",
            email="owner@example.com",
            wallet_address="0x4444444444444444444444444444444444444444",
            circle_wallet_id="circle-sca",
            account_type="SCA",
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
        self.assertEqual(candidate["accountType"], "EOA")
        self.assertEqual(candidate["claimStatus"], "unclaimed")
        self.assertTrue(candidate["claimCode"].startswith("clm_"))
        self.assertNotEqual(candidate["claimCode"], "agent_beta")
        self.assertEqual(candidate["dashboard"]["balance"]["available"], 1.25)

    def test_claim_link_endpoint_creates_wallet_and_returns_urls(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {
                    "circleWalletId": "circle-wallet-1",
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "accountType": "EOA",
                    "mode": "circle",
                    "binding": {
                        "agentName": request.agentName,
                        "agentId": request.agentId,
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "circleWalletId": "circle-wallet-1",
                        "circleWalletSetId": "circle-wallet-set",
                        "blockchain": "BASE-SEPOLIA",
                        "mode": "circle",
                        "accountType": "EOA",
                        "updatedAt": main.now_iso(),
                    },
                }

        with patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://ledger.example.test"}), patch.object(
            main,
            "get_ledger_wallet_client",
            return_value=FakeWalletClient(),
        ):
            response = self.client.post(
                "/ledger/claims/link",
                json={
                    "agentId": "312586087945994240",
                    "agentName": "OpenClaw OntologyAgent",
                    "email": "OWNER@example.com",
                    "agentDescription": "OpenClaw profile bio",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], "312586087945994240")
        self.assertEqual(payload["agentName"], "OpenClaw OntologyAgent")
        self.assertEqual(payload["ownerEmail"], "owner@example.com")
        self.assertTrue(payload["claimCode"].startswith("clm_"))
        self.assertIn("claimCode=" + payload["claimCode"], payload["claimUrl"])
        self.assertIn("agentId=312586087945994240", payload["claimUrl"])
        self.assertEqual(
            payload["agentUrl"],
            "https://ledger.example.test/dashboard?agentId=312586087945994240",
        )
        self.assertEqual(payload["walletAddress"], "0x1111111111111111111111111111111111111111")
        self.assertEqual(payload["circleWalletId"], "circle-wallet-1")
        self.assertEqual(payload["accountType"], "EOA")

    def test_claim_link_endpoint_persists_claimable_account_without_wallet_ids(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {"mode": "mock"}

        with patch.dict(os.environ, {"PUBLIC_BASE_URL": "   "}), patch.object(
            main,
            "get_ledger_wallet_client",
            return_value=FakeWalletClient(),
        ):
            response = self.client.post(
                "/ledger/claims/link",
                json={
                    "agentId": "312586087945994240",
                    "agentName": "OpenClaw OntologyAgent",
                    "email": "OWNER@example.com",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], "312586087945994240")
        self.assertEqual(payload["agentName"], "OpenClaw OntologyAgent")
        self.assertEqual(payload["ownerEmail"], "owner@example.com")
        self.assertIn("claimCode=" + payload["claimCode"], payload["claimUrl"])
        self.assertEqual(
            payload["agentUrl"],
            "https://ledger.curawealth.ai/dashboard?agentId=312586087945994240",
        )

        account = main.get_store().load().accounts[0]
        self.assertEqual(account.agentId, "312586087945994240")
        self.assertEqual(account.agentName, "OpenClaw OntologyAgent")
        self.assertEqual(account.email, "owner@example.com")

        claimable = self.client.get(
            "/dashboard/claimable-agents?email=owner@example.com"
        ).json()
        self.assertEqual(len(claimable["agents"]), 1)
        self.assertEqual(claimable["agents"][0]["agentId"], "312586087945994240")

    def test_claim_link_endpoint_rejects_non_eoa_wallets(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {
                    "circleWalletId": "circle-wallet-sca",
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "accountType": "SCA",
                    "mode": "circle",
                    "binding": {
                        "agentName": request.agentName,
                        "agentId": request.agentId,
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "circleWalletId": "circle-wallet-sca",
                        "circleWalletSetId": "circle-wallet-set",
                        "blockchain": "BASE-SEPOLIA",
                        "mode": "circle",
                        "accountType": "SCA",
                        "updatedAt": main.now_iso(),
                    },
                }

        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post(
                "/ledger/claims/link",
                json={
                    "agentId": "312586087945994240",
                    "agentName": "OpenClaw OntologyAgent",
                    "email": "owner@example.com",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "claim wallet must be an EOA Circle wallet")
        self.assertEqual(self.client.get("/ledger/state").json()["accounts"], [])

    def test_claim_link_endpoint_requires_profile_identity(self) -> None:
        response = self.client.post(
            "/ledger/claims/link",
            json={
                "agentId": "",
                "agentName": "OpenClaw OntologyAgent",
                "email": "owner@example.com",
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_claim_link_endpoint_requires_email_via_route_logic(self) -> None:
        response = self.client.post(
            "/ledger/claims/link",
            json={
                "agentId": "312586087945994240",
                "agentName": "OpenClaw OntologyAgent",
                "email": "   ",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "email is required")

    def test_dashboard_claimable_agents_can_load_without_chief_email(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_eigenflux",
            agent_name="EigenFlux Worker",
            email="agent-bound@example.com",
            wallet_address="0x4444444444444444444444444444444444444444",
            circle_wallet_id="circle-eigenflux",
            account_type="EOA",
        )

        response = self.client.get("/dashboard/claimable-agents")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["email"])
        self.assertEqual(len(payload["agents"]), 1)
        candidate = payload["agents"][0]
        self.assertEqual(candidate["agentId"], "agent_eigenflux")
        self.assertEqual(candidate["ownerEmail"], "agent-bound@example.com")
        self.assertEqual(candidate["accountType"], "EOA")
        self.assertTrue(candidate["claimCode"].startswith("clm_"))

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
                "if (url === '/admin/ledger/state') return { ok: true, json: async () => ({"
                "accounts: [{ agentId: 'agent_buyer', email: 'buyer@example.com', walletAddress: '0x1111111111111111111111111111111111111111', circleUsdcBalance: '1.98', gatewayUsdcAvailable: '0.75', gatewayUsdcTotal: '1.25', gatewayUsdcWithdrawable: '0.5', gatewayUsdcWithdrawing: '0.5', gatewayUsdcPendingDeposits: '2.25', gatewayPendingDepositsAtomic: '2250000', gatewayUsdcPendingBatch: '0.1', gatewayPendingBatchAtomic: '100000', availableAtomic: '5000000', lockedAtomic: '3000000' }],"
                "entries: [{ entryId: 'entry_1', entryType: 'credit', agentId: 'agent_buyer' }],"
                "escrows: [{ escrowId: 'escrow_1', buyerAgentId: 'agent_buyer', sellerAgentId: 'agent_seller', amountAtomic: '3000000', status: 'locked' }],"
                "onrampSessions: [{ sessionId: 'onramp_1', agentId: 'agentA', paymentAmount: '10.00', status: 'created', onrampUrl: 'https://pay.coinbase.com/buy/select-asset?sessionToken=abc' }]"
                "}) };"
                "if (url === '/ledger/wallets/get-or-create') return { ok: true, json: async () => ({"
                "wallet: { walletAddress: '0x1111111111111111111111111111111111111111', circleWalletId: 'circle-wallet-1' },"
                "account: { agentId: 'agent_research', availableAtomic: '0', lockedAtomic: '0' }"
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
                "process.stdout.write(JSON.stringify({"
                "stateHtml: elements.get('ledger-state').innerHTML,"
                "stateText: elements.get('ledger-state').textContent,"
                "escrowSelection: selectedEscrowId(),"
                "walletListener: listeners.get('wallet-form:submit'),"
                "settlementListener: listeners.get('settlement-form:submit'),"
                "stateCall: fetchCalls.find((call) => call.url === '/admin/ledger/state'),"
                "walletCall: fetchCalls.find((call) => call.url === '/ledger/wallets/get-or-create'),"
                "openCall: window.openCalls[0] || null,"
                "walletOutput: elements.get('wallet-output').textContent"
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
        self.assertIn("Circle USDC Available", output["stateHtml"])
        self.assertIn("1.98", output["stateHtml"])
        self.assertIn("Gateway USDC Available", output["stateHtml"])
        self.assertIn("0.75", output["stateHtml"])
        self.assertIn("Gateway Total", output["stateHtml"])
        self.assertIn("1.25", output["stateHtml"])
        self.assertIn("Gateway Withdrawable", output["stateHtml"])
        self.assertIn("0.5", output["stateHtml"])
        self.assertIn("Pending Deposits", output["stateHtml"])
        self.assertIn("2.25", output["stateHtml"])
        self.assertIn("Pending Deposits Atomic", output["stateHtml"])
        self.assertIn("2,250,000", output["stateHtml"])
        self.assertIn("Pending Batch", output["stateHtml"])
        self.assertIn("0.1", output["stateHtml"])
        self.assertIn("Pending Batch Atomic", output["stateHtml"])
        self.assertIn("100,000", output["stateHtml"])
        self.assertIn("Ledger Locked", output["stateHtml"])
        self.assertIn("3,000,000", output["stateHtml"])
        self.assertIn("agent_buyer", output["stateHtml"])
        self.assertIn("Email", output["stateHtml"])
        self.assertIn("buyer@example.com", output["stateHtml"])
        self.assertIn("0x1111111111111111111111111111111111111111", output["stateHtml"])
        self.assertIn("agent_seller", output["stateHtml"])
        self.assertIn("escrow_1", output["stateHtml"])
        self.assertIn("onramp_1", output["stateHtml"])
        self.assertIn("entry_1", output["stateHtml"])
        self.assertEqual(output["stateCall"]["method"], "GET")
        self.assertNotIn("{", output["stateHtml"])
        self.assertNotIn('"accounts"', output["stateHtml"])
        self.assertEqual(output["escrowSelection"], "escrow_1")
        self.assertEqual(output["walletListener"], "getOrCreateWallet")
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
        self.assertIsNone(output["openCall"])
        self.assertIn("agent_research", output["walletOutput"])

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

        state = self.client.get("/ledger/state?agentId=agent_buyer").json()
        self.assertEqual(len(state["entries"]), 1)
        self.assertEqual(state["entries"][0]["entryType"], "credit")

    def test_wallet_get_or_create_creates_zero_balance_ledger_account(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {
                    "circleWalletId": "circle-wallet-1",
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "accountType": "EOA",
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
                        "accountType": "EOA",
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
        self.assertEqual(payload["account"]["accountType"], "EOA")
        self.assertEqual(payload["account"]["availableAtomic"], "0")
        self.assertEqual(payload["account"]["lockedAtomic"], "0")

        state = self.client.get("/ledger/state?agentId=agent_research").json()
        self.assertEqual(len(state["accounts"]), 1)
        self.assertEqual(state["accounts"][0]["agentId"], "agent_research")
        self.assertEqual(state["accounts"][0]["agentName"], "Research Agent")
        self.assertEqual(state["accounts"][0]["email"], "agent@example.com")
        self.assertEqual(
            state["accounts"][0]["walletAddress"],
            "0x1111111111111111111111111111111111111111",
        )
        self.assertEqual(state["accounts"][0]["circleWalletId"], "circle-wallet-1")
        self.assertEqual(state["accounts"][0]["accountType"], "EOA")
        self.assertEqual(state["entries"], [])

    def test_gateway_deposit_proxies_to_wallet_rest_client(self) -> None:
        class FakeWalletClient:
            async def gateway_deposit(self, request):
                self.request = request
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "mode": "gateway_deposit",
                    "gatewayBalance": {"availableAtomic": request.amountAtomic},
                }

        fake_client = FakeWalletClient()
        with patch.object(main, "get_ledger_wallet_client", return_value=fake_client):
            response = self.client.post(
                "/ledger/gateway/deposits",
                json={
                    "agentId": "agent_research",
                    "amountAtomic": "1000",
                    "refId": "deposit:test",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], "agent_research")
        self.assertEqual(payload["amountAtomic"], "1000")
        self.assertEqual(payload["mode"], "gateway_deposit")
        self.assertEqual(fake_client.request.refId, "deposit:test")

    def test_circle_wallet_webhook_sweeps_inbound_usdc_to_gateway_once(self) -> None:
        main.get_store().bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        class FakeWalletClient:
            def __init__(self) -> None:
                self.requests = []

            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "2.23"}}

            async def gateway_deposit(self, request):
                self.requests.append(request)
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "mode": "gateway_deposit",
                    "gatewayBalance": {"availableAtomic": request.amountAtomic},
                }

        fake_client = FakeWalletClient()
        payload = {
            "subscriptionId": "subscription-1",
            "notificationId": "notification-1",
            "notificationType": "transactions.inbound",
            "notification": {
                "id": "tx-inbound-1",
                "state": "COMPLETE",
                "transactionType": "INBOUND",
                "walletId": "circle-wallet-1",
                "destinationAddress": "0x1111111111111111111111111111111111111111",
                "amounts": ["1.23"],
                "tokenSymbol": "USDC",
                "contractAddress": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            },
            "timestamp": "2026-05-21T06:00:00Z",
            "version": 2,
        }

        with patch.object(main, "get_ledger_wallet_client", return_value=fake_client):
            first = self.client.post("/circle/webhooks/wallets", json=payload)
            second = self.client.post("/circle/webhooks/wallets", json=payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "processed")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "duplicate")
        self.assertEqual(len(fake_client.requests), 1)
        self.assertEqual(fake_client.requests[0].agentId, "agent_research")
        self.assertEqual(fake_client.requests[0].amountAtomic, "2230000")
        self.assertEqual(fake_client.requests[0].refId, "circle-webhook:notification-1")

        state = self.client.get("/ledger/state?agentId=agent_research").json()
        self.assertEqual(len(state["circleWebhookEvents"]), 1)
        self.assertEqual(state["circleWebhookEvents"][0]["status"], "processed")
        self.assertEqual(state["circleWebhookEvents"][0]["transactionId"], "tx-inbound-1")

    def test_circle_wallet_webhook_sweeps_confirmed_inbound_usdc_to_gateway(self) -> None:
        main.get_store().bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        class FakeWalletClient:
            def __init__(self) -> None:
                self.requests = []

            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "2.01"}}

            async def gateway_deposit(self, request):
                self.requests.append(request)
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "mode": "gateway_deposit",
                }

        fake_client = FakeWalletClient()
        with patch.object(main, "get_ledger_wallet_client", return_value=fake_client):
            response = self.client.post(
                "/circle/webhooks/wallets",
                json={
                    "subscriptionId": "subscription-1",
                    "notificationId": "notification-confirmed",
                    "notificationType": "transactions.inbound",
                    "notification": {
                        "id": "tx-inbound-confirmed",
                        "state": "CONFIRMED",
                        "transactionType": "INBOUND",
                        "walletId": "circle-wallet-1",
                        "destinationAddress": "0x1111111111111111111111111111111111111111",
                        "amounts": ["1"],
                        "tokenId": "bdf128b4-827b-5267-8f9e-243694989b5f",
                    },
                    "timestamp": "2026-05-21T06:00:00Z",
                    "version": 2,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "processed")
        self.assertEqual(len(fake_client.requests), 1)
        self.assertEqual(fake_client.requests[0].amountAtomic, "2010000")

    def test_circle_wallet_webhook_skips_gateway_deposit_until_wallet_balance_exceeds_two_usdc(self) -> None:
        main.get_store().bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        class FakeWalletClient:
            def __init__(self) -> None:
                self.deposits = []

            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "2.00"}}

            async def gateway_deposit(self, request):
                self.deposits.append(request)
                raise AssertionError("wallet balance at threshold must not be swept")

        fake_client = FakeWalletClient()
        with patch.object(main, "get_ledger_wallet_client", return_value=fake_client):
            response = self.client.post(
                "/circle/webhooks/wallets",
                json={
                    "subscriptionId": "subscription-1",
                    "notificationId": "notification-threshold",
                    "notificationType": "transactions.inbound",
                    "notification": {
                        "id": "tx-inbound-threshold",
                        "state": "CONFIRMED",
                        "transactionType": "INBOUND",
                        "walletId": "circle-wallet-1",
                        "destinationAddress": "0x1111111111111111111111111111111111111111",
                        "amounts": ["2"],
                        "tokenSymbol": "USDC",
                    },
                    "timestamp": "2026-05-21T06:00:00Z",
                    "version": 2,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "skipped")
        self.assertEqual(response.json()["reason"], "wallet_balance_not_above_gateway_threshold")
        self.assertEqual(fake_client.deposits, [])

    def test_circle_wallet_webhook_skips_inbound_before_completion(self) -> None:
        main.get_store().bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        class FakeWalletClient:
            async def gateway_deposit(self, request):
                raise AssertionError("pending inbound transaction must not be swept")

        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post(
                "/circle/webhooks/wallets",
                json={
                    "subscriptionId": "subscription-1",
                    "notificationId": "notification-pending",
                    "notificationType": "transactions.inbound",
                    "notification": {
                        "id": "tx-inbound-pending",
                        "state": "PENDING",
                        "transactionType": "INBOUND",
                        "walletId": "circle-wallet-1",
                        "amounts": ["1.23"],
                        "tokenSymbol": "USDC",
                    },
                    "timestamp": "2026-05-21T06:00:00Z",
                    "version": 2,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "skipped")
        state = self.client.get("/ledger/state?agentId=agent_research").json()
        self.assertEqual(state["circleWebhookEvents"][0]["status"], "skipped")

    def test_circle_wallet_webhook_accepts_valid_circle_signature(self) -> None:
        os.environ["CIRCLE_WEBHOOK_VERIFY_SIGNATURE"] = "true"
        os.environ["CIRCLE_API_KEY"] = "test-circle-api-key"
        main.get_store().bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key_der = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        body = json.dumps(
            {
                "subscriptionId": "subscription-1",
                "notificationId": "notification-signed",
                "notificationType": "transactions.inbound",
                "notification": {
                    "id": "tx-inbound-signed",
                    "state": "COMPLETE",
                    "transactionType": "INBOUND",
                    "walletId": "circle-wallet-1",
                    "amounts": ["0.5"],
                    "tokenSymbol": "USDC",
                },
                "timestamp": "2026-05-21T06:00:00Z",
                "version": 2,
            },
            separators=(",", ":"),
        )
        signature = private_key.sign(body.encode("utf-8"), ec.ECDSA(hashes.SHA256()))

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "data": {
                        "id": "public-key-1",
                        "algorithm": "ECDSA_SHA_256",
                        "publicKey": base64.b64encode(public_key_der).decode("ascii"),
                    }
                }

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                pass

            async def get(self, url, headers):
                self.url = url
                self.headers = headers
                return FakeResponse()

        class FakeWalletClient:
            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "2.50"}}

            async def gateway_deposit(self, request):
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "mode": "gateway_deposit",
                }

        with (
            patch.object(main.httpx, "AsyncClient", FakeAsyncClient),
            patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()),
        ):
            response = self.client.post(
                "/circle/webhooks/wallets",
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-circle-key-id": "public-key-1",
                    "x-circle-signature": base64.b64encode(signature).decode("ascii"),
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "processed")

    def test_gateway_withdrawal_proxies_to_wallet_rest_client(self) -> None:
        class FakeWalletClient:
            async def gateway_withdraw(self, request):
                self.request = request
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "recipientAddress": request.recipientAddress,
                    "mode": "gateway_withdraw",
                    "mintTransactionHash": "0xmint",
                }

        fake_client = FakeWalletClient()
        with patch.object(main, "get_ledger_wallet_client", return_value=fake_client):
            response = self.client.post(
                "/ledger/gateway/withdrawals",
                json={
                    "agentId": "agent_research",
                    "amountAtomic": "1000",
                    "recipientAddress": "0x1111111111111111111111111111111111111111",
                    "refId": "withdraw:test",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], "agent_research")
        self.assertEqual(payload["amountAtomic"], "1000")
        self.assertEqual(payload["mode"], "gateway_withdraw")
        self.assertEqual(fake_client.request.refId, "withdraw:test")

    def test_ledger_state_includes_circle_usdc_balance_for_bound_accounts(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                assert circle_wallet_id == "circle-wallet-1"
                return {
                    "balances": {"USDC": "1.98"},
                    "gatewayBalance": {
                        "availableAtomic": "750000",
                        "totalAtomic": "1250000",
                        "withdrawableAtomic": "500000",
                        "withdrawingAtomic": "500000",
                        "pendingDepositsAtomic": "2250000",
                        "pendingBatchAtomic": "100000",
                        "formattedAvailable": "0.75",
                        "formattedTotal": "1.25",
                        "formattedWithdrawable": "0.5",
                        "formattedWithdrawing": "0.5",
                        "formattedPendingDeposits": "2.25",
                        "formattedPendingBatch": "0.1",
                    },
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_research",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            state = self.client.get("/ledger/state?agentId=agent_research").json()

        self.assertEqual(state["accounts"][0]["circleUsdcBalance"], "1.98")
        self.assertEqual(state["accounts"][0]["gatewayUsdcAvailable"], "0.75")
        self.assertEqual(state["accounts"][0]["gatewayUsdcTotal"], "1.25")
        self.assertEqual(state["accounts"][0]["gatewayUsdcWithdrawable"], "0.5")
        self.assertEqual(state["accounts"][0]["gatewayUsdcWithdrawing"], "0.5")
        self.assertEqual(state["accounts"][0]["gatewayUsdcPendingDeposits"], "2.25")
        self.assertEqual(state["accounts"][0]["gatewayPendingDepositsAtomic"], "2250000")
        self.assertEqual(state["accounts"][0]["gatewayUsdcPendingBatch"], "0.1")
        self.assertEqual(state["accounts"][0]["gatewayPendingBatchAtomic"], "100000")

    def test_ledger_state_returns_no_data_without_agent_id(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_owner",
            agent_name="Owner Agent",
            email="owner@example.com",
            wallet_address=None,
            circle_wallet_id=None,
        )
        store.credit(
            agent_id="agent_owner",
            amount_atomic="5000000",
            reason="owner funding",
            metadata={},
        )

        state = self.client.get("/ledger/state").json()

        self.assertEqual(state["accounts"], [])
        self.assertEqual(state["entries"], [])
        self.assertEqual(state["escrows"], [])
        self.assertEqual(state["onrampSessions"], [])
        self.assertEqual(state["onrampEvents"], [])
        self.assertEqual(state["chainRecords"], [])
        self.assertEqual(state["settlementRecords"], [])

    def test_admin_ledger_state_returns_full_state(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_owner",
            agent_name="Owner Agent",
            email="owner@example.com",
            wallet_address=None,
            circle_wallet_id=None,
        )
        store.credit(
            agent_id="agent_owner",
            amount_atomic="5000000",
            reason="owner funding",
            metadata={},
        )

        state = self.client.get("/admin/ledger/state").json()

        self.assertEqual([account["agentId"] for account in state["accounts"]], ["agent_owner"])
        self.assertEqual([entry["agentId"] for entry in state["entries"]], ["agent_owner"])

    def test_ledger_state_can_be_scoped_to_agent_id(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_owner",
            agent_name="Owner Agent",
            email="owner@example.com",
            wallet_address=None,
            circle_wallet_id=None,
        )
        store.bind_account_wallet(
            agent_id="agent_counterparty",
            agent_name="Counterparty Agent",
            email="counterparty@example.com",
            wallet_address=None,
            circle_wallet_id=None,
        )
        store.bind_account_wallet(
            agent_id="agent_other",
            agent_name="Other Agent",
            email="other@example.com",
            wallet_address=None,
            circle_wallet_id=None,
        )
        store.credit(
            agent_id="agent_owner",
            amount_atomic="5000000",
            reason="owner funding",
            metadata={},
        )
        store.credit(
            agent_id="agent_other",
            amount_atomic="5000000",
            reason="other funding",
            metadata={},
        )
        store.create_escrow(
            buyer_agent_id="agent_owner",
            seller_agent_id="agent_counterparty",
            amount_atomic="1000000",
            task_id="owner_task",
            description=None,
            metadata={},
        )
        store.create_escrow(
            buyer_agent_id="agent_other",
            seller_agent_id="agent_counterparty",
            amount_atomic="1000000",
            task_id="other_task",
            description=None,
            metadata={},
        )

        state = self.client.get("/ledger/state?agentId=agent_owner").json()

        self.assertEqual(
            [account["agentId"] for account in state["accounts"]],
            ["agent_owner"],
        )
        self.assertEqual(
            {entry["agentId"] for entry in state["entries"]},
            {"agent_owner"},
        )
        self.assertEqual(
            [escrow["taskId"] for escrow in state["escrows"]],
            ["owner_task"],
        )

    def test_ledger_state_uses_circle_balance_as_agent_visible_available(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                assert circle_wallet_id == "circle-wallet-1"
                return {
                    "balances": {"USDC": "1.98"},
                    "gatewayBalance": {
                        "availableAtomic": "750000",
                        "totalAtomic": "1250000",
                        "withdrawableAtomic": "500000",
                        "withdrawingAtomic": "500000",
                        "pendingDepositsAtomic": "2250000",
                        "formattedAvailable": "0.75",
                        "formattedTotal": "1.25",
                        "formattedWithdrawable": "0.5",
                        "formattedWithdrawing": "0.5",
                        "formattedPendingDeposits": "2.25",
                    },
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_research",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            state = self.client.get("/ledger/state?agentId=agent_research").json()
            state_after_second_read = self.client.get("/ledger/state?agentId=agent_research").json()

        account = state["accounts"][0]
        self.assertEqual(account["circleUsdcBalance"], "1.98")
        self.assertEqual(account["gatewayUsdcAvailable"], "0.75")
        self.assertEqual(account["gatewayUsdcTotal"], "1.25")
        self.assertEqual(account["gatewayUsdcWithdrawable"], "0.5")
        self.assertEqual(account["gatewayUsdcWithdrawing"], "0.5")
        self.assertEqual(account["gatewayUsdcPendingDeposits"], "2.25")
        self.assertEqual(account["gatewayPendingDepositsAtomic"], "2250000")
        self.assertEqual(account["availableAtomic"], "1980000")
        self.assertNotIn("ledgerAvailableAtomic", account)
        self.assertEqual(account["balanceSource"], "circle")
        self.assertEqual(state["entries"], [])
        self.assertEqual(state_after_second_read["entries"], [])
        self.assertEqual(main.get_store().load().accounts[0].availableAtomic, "0")

    def test_ledger_state_helper_uses_circle_balance_as_agent_visible_available(
        self,
    ) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                assert circle_wallet_id == "circle-wallet-1"
                return {
                    "balances": {"USDC": "1.98"},
                    "gatewayBalance": {
                        "availableAtomic": "750000",
                        "totalAtomic": "1250000",
                        "withdrawableAtomic": "500000",
                        "withdrawingAtomic": "500000",
                        "pendingDepositsAtomic": "2250000",
                        "formattedAvailable": "0.75",
                        "formattedTotal": "1.25",
                        "formattedWithdrawable": "0.5",
                        "formattedWithdrawing": "0.5",
                        "formattedPendingDeposits": "2.25",
                    },
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_research",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )
        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            state = asyncio.run(main.ledger_state_with_circle_balances())

        account = state["accounts"][0]
        self.assertEqual(account["circleUsdcBalance"], "1.98")
        self.assertEqual(account["gatewayUsdcAvailable"], "0.75")
        self.assertEqual(account["gatewayUsdcTotal"], "1.25")
        self.assertEqual(account["gatewayUsdcWithdrawable"], "0.5")
        self.assertEqual(account["gatewayUsdcWithdrawing"], "0.5")
        self.assertEqual(account["gatewayUsdcPendingDeposits"], "2.25")
        self.assertEqual(account["gatewayPendingDepositsAtomic"], "2250000")
        self.assertEqual(account["availableAtomic"], "1980000")
        self.assertNotIn("ledgerAvailableAtomic", account)
        self.assertEqual(account["balanceSource"], "circle")

    def test_dashboard_data_uses_circle_usdc_as_available_balance(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                assert circle_wallet_id == "circle-wallet-1"
                return {"balances": {"USDC": "1.98"}}

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            dashboard = self.client.get("/dashboard/data?email=agent@example.com").json()

        self.assertEqual(
            dashboard["agents"]["agent_research"]["balance"]["available"],
            1.98,
        )
        self.assertEqual(main.get_store().load().accounts[0].availableAtomic, "0")

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

    def test_wallet_get_or_create_rejects_non_eoa_circle_wallets(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {
                    "circleWalletId": "circle-wallet-sca",
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "accountType": "SCA",
                    "mode": "circle",
                    "binding": {
                        "agentName": request.agentName,
                        "agentId": request.agentId,
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "circleWalletId": "circle-wallet-sca",
                        "circleWalletSetId": "circle-wallet-set",
                        "blockchain": "BASE-SEPOLIA",
                        "mode": "circle",
                        "accountType": "SCA",
                        "updatedAt": main.now_iso(),
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
        self.assertEqual(response.json()["detail"], "claim wallet must be an EOA Circle wallet")
        self.assertEqual(self.client.get("/ledger/state").json()["accounts"], [])

    def test_wallet_get_or_create_rest_route_creates_ledger_account(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {
                    "circleWalletId": "circle-wallet-1",
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "accountType": "EOA",
                    "mode": "circle",
                    "binding": {
                        "agentName": request.agentName,
                        "agentId": request.agentId,
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "circleWalletId": "circle-wallet-1",
                        "circleWalletSetId": "circle-wallet-set",
                        "blockchain": "BASE-SEPOLIA",
                        "mode": "circle",
                        "accountType": "EOA",
                        "updatedAt": main.now_iso(),
                    },
                }

        with patch.object(main, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post(
                "/ledger/wallets/get-or-create",
                json={
                    "agentName": "Research Agent",
                    "agentId": "agent_research",
                    "circleWalletId": "circle-wallet-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["account"]["agentId"], "agent_research")
        self.assertEqual(
            self.client.get("/ledger/state?agentId=agent_research").json()["accounts"][0]["agentId"],
            "agent_research",
        )

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

        state = self.client.get("/ledger/state?agentId=agentA").json()
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
        self.assertEqual(
            len(self.client.get("/ledger/state?agentId=agentA").json()["onrampSessions"]),
            1,
        )

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
        state = self.client.get("/ledger/state?agentId=agentA").json()
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
        state = self.client.get("/ledger/state?agentId=agentA").json()
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
            item["agentId"]: item for item in self.client.get("/ledger/state?agentId=agent_buyer").json()["accounts"]
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
                    chainHttpUrl="http://chain.test",
                    recorderAddress="0x000000000000000000000000000000000000dEaD",
                    txHash="0xtesttx",
                    mode="mock",
                    escrowId=escrow.escrowId if escrow is not None else None,
                    entryIds=[entry.entryId for entry in entries],
                    payload=payload,
                    actionResult={
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
        state = self.client.get("/ledger/state?agentId=agent_buyer").json()
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
        state = self.client.get("/ledger/state?agentId=agent_buyer").json()
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
            item["agentId"]: item for item in self.client.get("/ledger/state?agentId=agent_buyer").json()["accounts"]
        }
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "2000000")
        self.assertEqual(accounts["agent_buyer"]["lockedAtomic"], "0")
        seller_accounts = {
            item["agentId"]: item for item in self.client.get("/ledger/state?agentId=agent_seller").json()["accounts"]
        }
        self.assertEqual(seller_accounts["agent_seller"]["availableAtomic"], "3000000")

    def test_release_escrow_persists_settlement_record_when_enabled(self) -> None:
        class FakeSettlementClient:
            enabled = True

            async def submit_release(self, escrow):
                current = main.now_iso()
                return main.LedgerSettlementRecord(
                    recordId="settle_test",
                    eventType="escrow_release",
                    status="submitted",
                    settlementHttpUrl="http://circle.test",
                    escrowId=escrow.escrowId,
                    fromAgentId=escrow.buyerAgentId,
                    toAgentId=escrow.sellerAgentId,
                    amountAtomic=escrow.amountAtomic,
                    transactionId="circle-tx-1",
                    transactionHash="0xrealtransfer",
                    transactionState="INITIATED",
                    mode="circle",
                    actionResult={"transactionHash": "0xrealtransfer"},
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
        state = self.client.get("/ledger/state?agentId=agent_buyer").json()
        self.assertEqual(len(state["settlementRecords"]), 1)
        self.assertEqual(state["settlementRecords"][0]["status"], "submitted")

    def test_agent_transfer_calls_circle_then_records_ledger_entries(self) -> None:
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
                    settlementHttpUrl="http://circle.test",
                    transferId=ref_id,
                    fromAgentId=from_agent_id,
                    toAgentId=to_agent_id,
                    amountAtomic=amount_atomic,
                    transactionId="circle-transfer-1",
                    transactionHash="0xagenttransfer",
                    transactionState="INITIATED",
                    mode="circle",
                    actionResult={"transactionHash": "0xagenttransfer"},
                    createdAt=current,
                    updatedAt=current,
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
        self.assertEqual(accounts["agent_sender"].availableAtomic, "0")
        self.assertEqual(accounts["agent_sender"].lockedAtomic, "0")
        self.assertEqual(accounts["agent_receiver"].availableAtomic, "0")
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
                    settlementHttpUrl="http://circle.test",
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

        self.assertEqual(response.status_code, 424)
        detail = response.json()["detail"]
        self.assertEqual(detail["message"], "Circle transfer failed")
        self.assertEqual(detail["settlementRecord"]["status"], "failed")
        self.assertEqual(detail["settlementRecord"]["error"], "Circle transfer failed")
        accounts = {
            item.agentId: item for item in main.get_store().load().accounts
        }
        self.assertEqual(accounts["agent_sender"].availableAtomic, "5000000")
        self.assertEqual(accounts["agent_receiver"].availableAtomic, "0")
        settlement_records = main.get_store().load().settlementRecords
        self.assertEqual(len(settlement_records), 1)
        self.assertEqual(settlement_records[0].status, "failed")
        self.assertEqual(settlement_records[0].error, "Circle transfer failed")

    def test_withdrawal_calls_circle_then_records_ledger_entry(self) -> None:
        class FakeSettlementClient:
            def __init__(self) -> None:
                self.calls = []

            async def submit_withdrawal(
                self,
                *,
                from_agent_id,
                to_address,
                amount_atomic,
                ref_id,
            ):
                self.calls.append(
                    {
                        "fromAgentId": from_agent_id,
                        "toAddress": to_address,
                        "amountAtomic": amount_atomic,
                        "refId": ref_id,
                    }
                )
                current = main.now_iso()
                return main.LedgerSettlementRecord(
                    recordId="settle_withdrawal",
                    eventType="withdrawal",
                    status="submitted",
                    settlementHttpUrl="http://circle.test",
                    transferId=ref_id,
                    fromAgentId=from_agent_id,
                    toAddress=to_address,
                    amountAtomic=amount_atomic,
                    transactionId="circle-withdrawal-1",
                    transactionHash="0xwithdrawal",
                    transactionState="INITIATED",
                    mode="circle",
                    actionResult={"transactionHash": "0xwithdrawal"},
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
        fake_settlement = FakeSettlementClient()

        with patch.object(
            main, "get_ledger_settlement_client", return_value=fake_settlement
        ):
            response = self.client.post(
                "/ledger/withdrawals",
                json={
                    "agentId": "agent_sender",
                    "ownerEmail": "sender@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "1250000",
                    "reason": "dashboard withdrawal",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["settlementRecord"]["transactionHash"], "0xwithdrawal")
        self.assertEqual(fake_settlement.calls[0]["fromAgentId"], "agent_sender")
        self.assertEqual(
            fake_settlement.calls[0]["toAddress"],
            "0x2222222222222222222222222222222222222222",
        )
        self.assertEqual(payload["account"]["availableAtomic"], "3750000")
        self.assertEqual(payload["entry"]["entryType"], "withdrawal")
        self.assertEqual(
            payload["entry"]["metadata"]["destinationAddress"],
            "0x2222222222222222222222222222222222222222",
        )
        self.assertTrue(payload["entry"]["metadata"]["counterparty"].startswith("External"))
        self.assertEqual(payload["route"]["method"], "circle_withdrawal")

    def test_withdrawal_records_submitted_and_withdrawn_entries(self) -> None:
        class FakeSettlementClient:
            async def submit_withdrawal(self, **kwargs):
                return main.LedgerSettlementRecord(
                    recordId="settle_withdrawal",
                    eventType="withdrawal",
                    settlementHttpUrl="http://settlement.test",
                    transferId=kwargs["ref_id"],
                    fromAgentId=kwargs["from_agent_id"],
                    toAddress=kwargs["to_address"],
                    asset="USDC",
                    amountAtomic=kwargs["amount_atomic"],
                    status="submitted",
                    transactionHash="0xwithdrawal",
                    actionResult={
                        "transactionHash": "0xwithdrawal",
                        "estimatedGasFeeAtomic": "3000",
                        "estimatedGasFee": "0.003",
                        "netAmountAtomic": "997000",
                        "netAmount": "0.997",
                    },
                    createdAt=main.now_iso(),
                    updatedAt=main.now_iso(),
                )

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_withdraw",
            agent_name="Withdraw Agent",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-withdraw",
        )
        store.credit(
            agent_id="agent_withdraw",
            amount_atomic="2000000",
            reason="seed",
            metadata={},
        )

        with patch.object(main, "get_ledger_settlement_client", return_value=FakeSettlementClient()):
            response = self.client.post(
                "/ledger/withdrawals",
                json={
                    "agentId": "agent_withdraw",
                    "ownerEmail": "owner@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "1000000",
                    "reason": "dashboard withdrawal",
                    "metadata": {"source": "dashboard"},
                },
            )

        self.assertEqual(response.status_code, 200)
        statuses = [
            entry["metadata"].get("dashboardStatus")
            for entry in response.json()["entries"]
        ]
        self.assertEqual(statuses, ["withdraw_submitted", "withdrawn"])

    def test_withdrawal_failure_does_not_mutate_ledger_balance(self) -> None:
        class FakeSettlementClient:
            async def submit_withdrawal(self, **_kwargs):
                current = main.now_iso()
                record = main.LedgerSettlementRecord(
                    recordId="settle_failed_withdrawal",
                    eventType="withdrawal",
                    status="failed",
                    settlementHttpUrl="http://circle.test",
                    transferId="withdrawal_failed",
                    fromAgentId="agent_sender",
                    toAddress="0x2222222222222222222222222222222222222222",
                    amountAtomic="1250000",
                    error="Circle withdrawal failed",
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

        with patch.object(
            main, "get_ledger_settlement_client", return_value=FakeSettlementClient()
        ):
            response = self.client.post(
                "/ledger/withdrawals",
                json={
                    "agentId": "agent_sender",
                    "ownerEmail": "sender@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "1250000",
                },
            )

        self.assertEqual(response.status_code, 424)
        detail = response.json()["detail"]
        self.assertEqual(detail["message"], "Circle withdrawal failed")
        self.assertEqual(detail["settlementRecord"]["status"], "failed")
        account = main.get_store().load().accounts[0]
        self.assertEqual(account.availableAtomic, "5000000")
        state = main.get_store().load()
        settlement_records = state.settlementRecords
        self.assertEqual(len(settlement_records), 1)
        self.assertEqual(settlement_records[0].status, "failed")
        withdrawal_entries = [
            entry for entry in state.entries if entry.entryType == "withdrawal"
        ]
        self.assertEqual(withdrawal_entries, [])
        lifecycle_entries = [
            entry
            for entry in state.entries
            if entry.entryType == "withdrawal_submitted"
        ]
        self.assertEqual(len(lifecycle_entries), 2)
        submitted_entry, failed_entry = lifecycle_entries
        self.assertEqual(submitted_entry.availableDeltaAtomic, "0")
        self.assertEqual(failed_entry.availableDeltaAtomic, "0")
        self.assertEqual(
            failed_entry.metadata["linkedEntryId"],
            submitted_entry.entryId,
        )
        self.assertEqual(failed_entry.metadata["dashboardStatus"], "failed")
        self.assertEqual(
            failed_entry.metadata["destinationAddress"],
            main.normalize_evm_address("0x2222222222222222222222222222222222222222"),
        )

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

        self.assertEqual(response.status_code, 424)
        self.assertEqual(
            response.json()["detail"]["message"],
            "Circle settlement is required for direct agent transfers",
        )
        accounts = {
            item.agentId: item for item in main.get_store().load().accounts
        }
        self.assertEqual(accounts["agent_sender"].availableAtomic, "5000000")
        self.assertEqual(accounts["agent_receiver"].availableAtomic, "0")

    def test_settlement_client_uses_dedicated_circle_http_url(self) -> None:
        os.environ["LEDGER_SETTLEMENT_ENABLED"] = "true"
        os.environ["LEDGER_SETTLEMENT_HTTP_URL"] = "http://circle.test"
        main.get_ledger_settlement_client.cache_clear()

        client = main.get_ledger_settlement_client()

        self.assertTrue(client.enabled)
        self.assertEqual(client.settlement_http_url, "http://circle.test")

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
                    settlementHttpUrl="http://circle.test",
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

        self.assertEqual(response.status_code, 424)
        state = self.client.get("/ledger/state?agentId=agent_buyer").json()
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
            item["agentId"]: item for item in self.client.get("/ledger/state?agentId=agent_buyer").json()["accounts"]
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

    def test_credit_rejects_zero_amount(self) -> None:
        response = self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "0", "reason": "bad funding"},
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

        state = reloaded_client.get("/ledger/state?agentId=agent_buyer").json()

        self.assertEqual(state["accounts"][0]["agentId"], "agent_buyer")
        self.assertEqual(state["accounts"][0]["availableAtomic"], "5000000")
        self.assertTrue(Path(self.state_path).exists())

    def test_route_payment_intent_is_served_by_ledger_rest(self) -> None:
        response = self.client.post(
            "/ledger/payment/route",
            json={
                "purpose": "paid api",
                "deliveryMode": "immediate_api",
                "requiresAcceptance": False,
                "externalService": True,
                "serviceUrl": "https://seller.example/x402",
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["method"], "x402")
        self.assertEqual(result["allowedTools"], ["chain_x402_fetch"])

    def test_route_payment_intent_supports_funding_onramp(self) -> None:
        response = self.client.post(
            "/ledger/payment/route",
            json={
                "purpose": "fund agent wallet",
                "deliveryMode": "funding",
                "requiresAcceptance": False,
                "externalService": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["method"], "onramp")
        self.assertEqual(result["allowedTools"], ["agent_wallet_create_onramp_session"])

    def test_route_payment_intent_supports_direct_agent_transfer(self) -> None:
        response = self.client.post(
            "/ledger/payment/route",
            json={
                "purpose": "pay another agent now",
                "deliveryMode": "agent_transfer",
                "requiresAcceptance": False,
                "externalService": False,
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["method"], "gateway_nanopayment")
        self.assertEqual(result["allowedTools"], ["agent_wallet_transfer"])

    def test_route_payment_intent_supports_withdrawal(self) -> None:
        response = self.client.post(
            "/ledger/payment/route",
            json={
                "purpose": "withdraw USDC to an external wallet",
                "deliveryMode": "withdrawal",
                "requiresAcceptance": False,
                "externalService": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["method"], "circle_withdrawal")
        self.assertEqual(result["allowedTools"], ["agent_wallet_settle_ledger_transfer"])

    def test_ledger_rest_routes_operate_on_local_store(self) -> None:
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
                "taskId": "task_123",
                "description": "Research task",
            },
        ).json()["escrow"]
        released = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release").json()[
            "escrow"
        ]
        state = self.client.get("/admin/ledger/state").json()

        self.assertEqual(released["status"], "released")
        accounts = {item["agentId"]: item for item in state["accounts"]}
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "2000000")
        self.assertEqual(accounts["agent_buyer"]["lockedAtomic"], "0")
        self.assertEqual(accounts["agent_seller"]["availableAtomic"], "3000000")

        self.client.post(
            "/ledger/accounts/agent_refund_buyer/credit",
            json={"amountAtomic": "4000000", "reason": "demo funding"},
        )
        refund_escrow = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_refund_buyer",
                "sellerAgentId": "agent_refund_seller",
                "amountAtomic": "1000000",
            },
        ).json()["escrow"]
        refunded = self.client.post(
            f"/ledger/escrows/{refund_escrow['escrowId']}/refund"
        ).json()["escrow"]

        self.assertEqual(refunded["status"], "refunded")

    def test_onramp_rest_route_creates_session(self) -> None:
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
        result = response.json()
        self.assertEqual(result["agentId"], "agentA")
        self.assertEqual(result["status"], "created")
        self.assertIn("onrampUrl", result)


if __name__ == "__main__":
    unittest.main()
