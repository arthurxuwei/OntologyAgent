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
from config import LEDGER_DASHBOARD_ASSETS_PATH
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
        source = html
        if LEDGER_DASHBOARD_ASSETS_PATH.exists():
            source += "\n" + "\n".join(
                path.read_text()
                for path in sorted(LEDGER_DASHBOARD_ASSETS_PATH.rglob("*"))
                if path.is_file()
            )
        self.assertIn("Agent Wallet · MVP Dashboard", html)
        self.assertIn('href="/dashboard/assets/dashboard.css"', html)
        self.assertIn('src="/dashboard/assets/dashboard/DashboardApp.jsx"', html)
        self.assertIn("githubLoginHref", source)
        self.assertIn('href={githubLoginHref}', source)
        self.assertIn("fetch('/auth/session'", source)
        self.assertIn("if (!registered) return <window.MvpGithubAuthScreen />", source)
        self.assertIn("if (!claimed)    return <window.MvpClaimScreen />", source)
        self.assertIn("window.ClaimForm = ClaimForm", source)
        self.assertIn("fetch(`/dashboard/claimable-agents?claimed=${claimed}`)", source)
        self.assertNotIn("email=${email}", source)
        self.assertNotIn("function ResetLink()", source)
        self.assertNotIn("<ResetLink />", source)
        self.assertNotIn("↻ {t('mvp.dash.reset')}", source)
        self.assertIn("t('mvp.dash.claim.code_label')", source)
        self.assertIn("const canValidate = trimmedCode.length > 0", source)
        self.assertIn("candidate.claimCode", source)
        self.assertIn("{t('mvp.dash.claim.validate_button')} →", source)
        self.assertIn("pending_settle", source)
        self.assertIn("pending_inbound_chain", source)
        self.assertIn("function InfoTrigger({ tooltipKey", source)
        self.assertIn("window.InfoTrigger = InfoTrigger", source)
        self.assertIn("window.STATUS_CHIP_META = STATUS_CHIP_META", source)
        self.assertIn("'mvp.dash.status.pending_settle.label':    { en: 'SETTLING'", source)
        self.assertIn("'mvp.dash.status.pending_inbound_chain.label': { en: 'CREDITING'", source)
        self.assertIn("'mvp.dash.status.withdrawn.label':          { en: 'WITHDRAWN'", source)
        self.assertIn("'mvp.dash.status.credited.label':           { en: 'CREDITED'", source)
        self.assertIn("mvp.dash.status.pending_settle.tooltip", source)
        self.assertIn("mvp.dash.status.pending_inbound_chain.tooltip", source)
        self.assertIn("mvp.dash.funding.gateway_explainer", source)
        self.assertIn("titleSuffix=", source)
        self.assertIn("withdraw_submitted", source)
        self.assertIn("credited", source)
        self.assertIn("withdrawn", source)
        self.assertIn("failed", source)
        self.assertIn("Gas", source)
        self.assertIn("Net", source)
        self.assertIn("Gas ~", source)
        self.assertIn("Net ", source)
        self.assertNotIn("`Gas ~$${gasDisplay}`", source)
        self.assertNotIn("`Net $${netDisplay}`", source)
        self.assertIn("Net to destination", source)
        self.assertIn("Network fee", source)
        self.assertIn("~0.003", source)
        self.assertNotIn("~$0.003", source)
        self.assertNotIn("mvp.dash.funding.min_hint':                    { en: 'Min 1 USDC · Network: Base · Est. gas ~$0.01", source)
        self.assertNotIn("<span style={{ fontSize: '20px', color: '#5B6B79' }}>$</span>", source)
        self.assertNotIn("{t('mvp.dash.onramp.fee_label')}: ${fee.toFixed(2)}", source)
        self.assertNotIn("{sign}${window.formatAmount(amount)}", source)
        self.assertNotIn("${window.formatAmount(balance.available)}", source)
        self.assertNotIn("${window.formatAmount(localBalance)}", source)
        self.assertNotIn("`$${window.formatAmount(balance.lifetimeIn)}`", source)
        self.assertNotIn("`$${window.formatAmount(balance.lifetimeOut)}`", source)
        self.assertNotIn("+${window.formatAmount(tx.amount)} USDC · {tx.counterparty}", source)
        self.assertNotIn("net $0.00", source)
        self.assertIn("Submitted", source)
        self.assertIn("gasFeeAtomic", source)
        self.assertIn("netAmountAtomic", source)
        self.assertIn("const displayAmountAtomic = (row, fallbackAtomic) => {", source)
        self.assertIn("const metadataAmount = meta.amountAtomic ? atomicToUsdc(meta.amountAtomic) : 0;", source)
        self.assertIn("return availableAmount > 0 ? row.availableDeltaAtomic : (metadataAmount > 0 ? meta.amountAtomic : fallbackAtomic);", source)
        self.assertIn("const rowDelta = atomicToUsdc(row.availableDeltaAtomic);", source)
        self.assertIn("return total + (rowDelta !== 0 ? Math.abs(rowDelta) : 0);", source)
        self.assertIn("const txAmountAtomic = displayAmountAtomic(row, amountAtomic);", source)
        self.assertIn("const statusOrder = meta.dashboardStatus === 'withdrawn' ? 2 : meta.dashboardStatus === 'withdraw_submitted' ? 1 : 0;", source)
        self.assertIn(".sort((a, b) => b.sortOrder - a.sortOrder)", source)
        self.assertIn("const previewTxs = stage === 'settled' ? localTxs.slice(0, Math.min(2, localTxs.length)) : [];", source)
        self.assertIn("{previewTxs.map((tx) => (", source)
        self.assertIn("role={tx.role}", source)
        self.assertIn("gasFee={tx.gasFee}", source)
        self.assertIn("gasFeeAtomic={tx.gasFeeAtomic}", source)
        self.assertIn("netAmount={tx.netAmount}", source)
        self.assertIn("netAmountAtomic={tx.netAmountAtomic}", source)
        self.assertIn("txHash={tx.txHash}", source)
        self.assertIn("network={tx.network}", source)
        self.assertIn("withdrawalLike", source)
        self.assertIn("shortTxHash", source)
        self.assertIn("title={txHash}", source)
        self.assertIn("Gateway Wallet", source)
        self.assertIn("PendingBalanceLine", source)
        self.assertIn("PendingDepositCard", source)
        self.assertIn("PENDING TOP-UP", source)
        self.assertIn("Base confirming", source)
        self.assertIn("Crediting Gateway Wallet", source)
        self.assertIn("const creditedLinkedEntryIds = new Set(", source)
        self.assertIn("tx.linkedEntryId || (tx.metadata && tx.metadata.linkedEntryId)", source)
        self.assertIn("return !creditedLinkedEntryIds.has(tx.id);", source)
        self.assertIn("overflowWrap: 'anywhere'", source)
        self.assertLess(
            source.index("{pendingDeposits.length > 0 && ("),
            source.index("title={t('mvp.dash.funding.add_label')}"),
        )
        self.assertNotIn("WITHDRAWING", source)
        self.assertNotIn("提现中", source)
        self.assertNotIn("DEMO CODE · paste any to try the flow", source)
        self.assertNotIn("function DemoHint", source)
        self.assertNotIn("demo_hint", source)
        self.assertIn("clm_…", source)
        self.assertIn("candidate.dashboard.agent.role", source)
        self.assertIn("const activeLabel = activeMeta && activeMeta.agent && activeMeta.agent.name", source)
        self.assertIn("{activeLabel}", source)
        self.assertNotIn("agent id / name", source)
        self.assertNotIn("{t('mvp.dash.claim.validate_button')} ->", source)
        self.assertNotIn("function ClaimStatusCard", source)
        self.assertNotIn("Looking up agents", source)
        self.assertNotIn("function CandidateButton", source)
        self.assertIn("window.localStorage.getItem(STORAGE_KEYS.mockState) || 'day1'", source)
        self.assertNotIn('type="email"', source)
        self.assertNotIn('placeholder="you@example.com"', source)
        self.assertNotIn("<window.MockStateToggle />", source)
        self.assertIn("fetch('/dashboard/data')", source)
        self.assertNotIn("const emailQuery = ownerEmail", source)
        self.assertIn("fetch('/onramp/sessions'", source)
        self.assertIn("fullWalletAddress", source)
        self.assertIn("mvp.dash.funding.add_description", source)
        self.assertIn("mvp.dash.funding.receive_eyebrow", source)
        self.assertIn("mvp.dash.funding.onramp_coming_soon", source)
        self.assertNotIn("<CardTitle>{t('mvp.dash.funding.onramp_title')}</CardTitle>", source)
        self.assertNotIn("setOnrampOpen(true)", source)
        self.assertIn("const qrAddress = String(address || '').trim();", source)
        self.assertIn("qr.addData(qrAddress);", source)
        self.assertIn("data-qr-address={qrAddress}", source)
        self.assertIn("jsQR.js", html)
        self.assertIn("fetch('/ledger/withdrawals'", source)
        self.assertIn("<window.AddressPicker", source)
        self.assertIn("<window.AddAddressModal", source)
        self.assertIn("wallets, defaultWalletId", source)
        self.assertIn("const amountAtomic = usdcToAtomic(amount);", source)
        self.assertIn("amountAtomic,", source)
        self.assertIn("ownerEmail", source)
        self.assertIn("const MIN_WITHDRAW = 1;", source)
        self.assertIn("withdrawAvailable", source)
        self.assertIn("const [withdrawBalance, setWithdrawBalance] = React.useState(Number(data.balance.withdrawAvailable", source)
        self.assertIn("typeof payload.detail === 'string'", source)
        self.assertIn("Min 1 USDC", source)
        self.assertNotIn("claimAgent('agentA')", source)
        self.assertNotIn("mvp.dash.settings.danger_button", source)
        self.assertNotIn("mvp.dash.settings.open_demo", source)

    def test_dashboard_assets_are_served_as_static_files(self) -> None:
        response = self.client.get("/dashboard/assets/dashboard/DashboardApp.jsx")

        self.assertEqual(response.status_code, 200)
        self.assertIn("function DashboardSurface()", response.text)

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
