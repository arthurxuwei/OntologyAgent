import asyncio
import base64
import json
import os
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

import auth
import httpx
import jwt
import main
import services
import webhooks
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from fastapi.testclient import TestClient
from helpers import LedgerServiceTestCase


class TestAuthRoutes(LedgerServiceTestCase):
    def test_health_returns_ok(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

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
            auth,
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
                auth,
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
        self.assertNotIn("function ResetLink()", html)
        self.assertNotIn("<ResetLink />", html)
        self.assertNotIn("↻ {t('mvp.dash.reset')}", html)
        self.assertIn("t('mvp.dash.claim.code_label')", html)
        self.assertIn("const canValidate = trimmedCode.length > 0", html)
        self.assertIn("candidate.claimCode", html)
        self.assertIn("{t('mvp.dash.claim.validate_button')} →", html)
        self.assertIn("pending_settle", html)
        self.assertIn("pending_inbound_chain", html)
        self.assertIn("function InfoTrigger({ tooltipKey", html)
        self.assertIn("window.InfoTrigger = InfoTrigger", html)
        self.assertIn("window.STATUS_CHIP_META = STATUS_CHIP_META", html)
        self.assertIn("mvp.dash.status.pending_settle.tooltip", html)
        self.assertIn("mvp.dash.status.pending_inbound_chain.tooltip", html)
        self.assertIn("mvp.dash.funding.gateway_explainer", html)
        self.assertIn("titleSuffix=", html)
        self.assertIn("withdraw_submitted", html)
        self.assertIn("credited", html)
        self.assertIn("withdrawn", html)
        self.assertIn("failed", html)
        self.assertIn("Gas", html)
        self.assertIn("Net", html)
        self.assertIn("Gas ~", html)
        self.assertIn("Net ", html)
        self.assertNotIn("`Gas ~$${gasDisplay}`", html)
        self.assertNotIn("`Net $${netDisplay}`", html)
        self.assertIn("Net to destination", html)
        self.assertIn("Network fee", html)
        self.assertIn("~0.003", html)
        self.assertNotIn("~$0.003", html)
        self.assertNotIn("mvp.dash.funding.min_hint':                    { en: 'Min 1 USDC · Network: Base · Est. gas ~$0.01", html)
        self.assertNotIn("<span style={{ fontSize: '20px', color: '#5B6B79' }}>$</span>", html)
        self.assertNotIn("{t('mvp.dash.onramp.fee_label')}: ${fee.toFixed(2)}", html)
        self.assertNotIn("{sign}${window.formatAmount(amount)}", html)
        self.assertNotIn("${window.formatAmount(balance.available)}", html)
        self.assertNotIn("${window.formatAmount(localBalance)}", html)
        self.assertNotIn("`$${window.formatAmount(balance.lifetimeIn)}`", html)
        self.assertNotIn("`$${window.formatAmount(balance.lifetimeOut)}`", html)
        self.assertNotIn("+${window.formatAmount(tx.amount)} USDC · {tx.counterparty}", html)
        self.assertNotIn("net $0.00", html)
        self.assertIn("Submitted", html)
        self.assertIn("gasFeeAtomic", html)
        self.assertIn("netAmountAtomic", html)
        self.assertIn("const displayAmountAtomic = (row, fallbackAtomic) => {", html)
        self.assertIn("const metadataAmount = meta.amountAtomic ? atomicToUsdc(meta.amountAtomic) : 0;", html)
        self.assertIn("return availableAmount > 0 ? row.availableDeltaAtomic : (metadataAmount > 0 ? meta.amountAtomic : fallbackAtomic);", html)
        self.assertIn("const rowDelta = atomicToUsdc(row.availableDeltaAtomic);", html)
        self.assertIn("return total + (rowDelta !== 0 ? Math.abs(rowDelta) : 0);", html)
        self.assertIn("const txAmountAtomic = displayAmountAtomic(row, amountAtomic);", html)
        self.assertIn("const statusOrder = meta.dashboardStatus === 'withdrawn' ? 2 : meta.dashboardStatus === 'withdraw_submitted' ? 1 : 0;", html)
        self.assertIn(".sort((a, b) => b.sortOrder - a.sortOrder)", html)
        self.assertIn("const previewTxs = stage === 'settled' ? localTxs.slice(0, Math.min(2, localTxs.length)) : [];", html)
        self.assertIn("{previewTxs.map((tx) => (", html)
        self.assertIn("role={tx.role}", html)
        self.assertIn("gasFee={tx.gasFee}", html)
        self.assertIn("gasFeeAtomic={tx.gasFeeAtomic}", html)
        self.assertIn("netAmount={tx.netAmount}", html)
        self.assertIn("netAmountAtomic={tx.netAmountAtomic}", html)
        self.assertIn("txHash={tx.txHash}", html)
        self.assertIn("network={tx.network}", html)
        self.assertIn("withdrawalLike", html)
        self.assertIn("shortTxHash", html)
        self.assertIn("title={txHash}", html)
        self.assertIn("Gateway Wallet", html)
        self.assertIn("PendingBalanceLine", html)
        self.assertIn("PendingDepositCard", html)
        self.assertIn("PENDING TOP-UP", html)
        self.assertIn("Base confirming", html)
        self.assertIn("Crediting Gateway Wallet", html)
        self.assertIn("const creditedLinkedEntryIds = new Set(", html)
        self.assertIn("tx.linkedEntryId || (tx.metadata && tx.metadata.linkedEntryId)", html)
        self.assertIn("return !creditedLinkedEntryIds.has(tx.id);", html)
        self.assertIn("overflowWrap: 'anywhere'", html)
        self.assertLess(
            html.index("{pendingDeposits.length > 0 && ("),
            html.index("title={t('mvp.dash.funding.add_label')}"),
        )
        self.assertNotIn("WITHDRAWING", html)
        self.assertNotIn("提现中", html)
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
