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
        self.assertIn("kovaloop_ledger_oauth_state=", response.headers["set-cookie"])

    def test_google_login_redirects_to_google_oauth(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GOOGLE_CLIENT_ID": "google-client",
                "GOOGLE_CLIENT_SECRET": "google-secret",
                "AUTH_SESSION_SECRET": "session-secret",
                "PUBLIC_BASE_URL": "https://ledger.example.test",
            },
        ):
            response = self.client.get("/auth/google/login", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        location = response.headers["location"]
        self.assertTrue(location.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        self.assertIn("client_id=google-client", location)
        self.assertIn("response_type=code", location)
        self.assertIn("scope=openid+email+profile", location)
        self.assertIn(
            "redirect_uri=https%3A%2F%2Fledger.example.test%2Fauth%2Fgoogle%2Fcallback",
            location,
        )
        self.assertIn("kovaloop_ledger_oauth_state=", response.headers["set-cookie"])

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
        self.assertIn("kovaloop_ledger_oauth_return=", set_cookie)
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
            self.assertIn("kovaloop_ledger_oauth_return=", set_cookie)
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
                        "kovaloop_ledger_oauth_state=oauth-state; "
                        "kovaloop_ledger_oauth_return=/dashboard%3FclaimCode%3Dclm_abc%26agentId%3Dagent_1"
                    )
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response.headers["location"],
            "/dashboard?claimCode=clm_abc&agentId=agent_1",
        )
        self.assertIn("kovaloop_ledger_oauth_return=", response.headers["set-cookie"])

    def test_google_callback_redirects_to_stored_claim_return_path_and_sets_session(self) -> None:
        async def fake_fetch_google_user(_code, redirect_uri=None):
            self.assertEqual(
                redirect_uri,
                "https://ledger.example.test/auth/google/callback",
            )
            return {
                "provider": "google",
                "login": "owner@example.com",
                "name": "Google User",
                "email": "OWNER@EXAMPLE.COM",
                "avatar_url": "https://example.test/avatar.png",
            }

        with patch.dict(
            os.environ,
            {
                "AUTH_SESSION_SECRET": "session-secret",
                "PUBLIC_BASE_URL": "https://ledger.example.test",
            },
        ), patch.object(
            auth,
            "fetch_google_user",
            side_effect=fake_fetch_google_user,
        ):
            response = self.client.get(
                "/auth/google/callback?code=abc&state=oauth-state",
                headers={
                    "Cookie": (
                        "kovaloop_ledger_oauth_state=oauth-state; "
                        "kovaloop_ledger_oauth_return=/dashboard%3FclaimCode%3Dclm_abc%26agentId%3Dagent_1"
                    )
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response.headers["location"],
            "/dashboard?claimCode=clm_abc&agentId=agent_1",
        )
        set_cookie = response.headers["set-cookie"]
        self.assertIn("kovaloop_ledger_session=", set_cookie)
        self.assertIn("kovaloop_ledger_oauth_return=", set_cookie)

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
                            "kovaloop_ledger_oauth_state=oauth-state; "
                            f"kovaloop_ledger_oauth_return={return_path}"
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
                    "kovaloop_ledger_oauth_state=oauth-state; "
                    "kovaloop_ledger_oauth_return=/dashboard%3FclaimCode%3Dclm_abc%26agentId%3Dagent_1"
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
        self.assertIn("kovaloop_ledger_oauth_state=", set_cookie)
        self.assertIn("kovaloop_ledger_oauth_return=", set_cookie)
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
        main.get_store().bind_account_wallet(
            agent_id="agent_existing",
            agent_name="Existing Agent",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-existing",
        )
        main.get_store().claim_dashboard_account(
            agent_id="agent_existing",
            email="owner@example.com",
            dashboard_email="owner@example.com",
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_unclaimed",
            agent_name="Unclaimed Agent",
            email="owner@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle-unclaimed",
        )
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
                headers={"Cookie": f"kovaloop_ledger_session={session}"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["user"]["provider"], "github")
        self.assertEqual(payload["user"]["login"], "octo")
        self.assertEqual(payload["user"]["email"], "owner@example.com")
        self.assertEqual(payload["claimedAgentIds"], ["agent_existing"])

    def test_auth_session_returns_signed_google_user(self) -> None:
        main.get_store().bind_account_wallet(
            agent_id="agent_existing",
            agent_name="Existing Agent",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-existing",
        )
        main.get_store().claim_dashboard_account(
            agent_id="agent_existing",
            email="owner@example.com",
            dashboard_email="owner@example.com",
        )
        with patch.dict(os.environ, {"AUTH_SESSION_SECRET": "session-secret"}):
            session = main.sign_auth_session(
                {
                    "provider": "google",
                    "login": "owner@example.com",
                    "name": "Google User",
                    "email": "OWNER@EXAMPLE.COM",
                    "avatar_url": "https://example.test/avatar.png",
                }
            )
            response = self.client.get(
                "/auth/session",
                headers={"Cookie": f"kovaloop_ledger_session={session}"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["user"]["provider"], "google")
        self.assertEqual(payload["user"]["login"], "owner@example.com")
        self.assertEqual(payload["user"]["email"], "owner@example.com")
        self.assertEqual(payload["claimedAgentIds"], ["agent_existing"])

    def test_auth_logout_clears_session_cookie(self) -> None:
        response = self.client.post("/auth/logout")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        set_cookie = response.headers["set-cookie"]
        self.assertIn("kovaloop_ledger_session=", set_cookie)
        self.assertIn("Max-Age=0", set_cookie)

    def test_auth_logout_get_clears_session_cookie_and_returns_to_dashboard(self) -> None:
        response = self.client.get("/auth/logout", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/dashboard")
        set_cookie = response.headers["set-cookie"]
        self.assertIn("kovaloop_ledger_session=", set_cookie)
        self.assertIn("Max-Age=0", set_cookie)

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
        with patch.dict(os.environ, {"ADMIN_TOKEN": "admin-secret"}):
            response = self.client.get("/admin?token=admin-secret", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.history[0].status_code, 307)
        self.assertEqual(response.history[0].headers["location"], "/admin")
        html = response.text
        self.assertIn("Kovaloop Ledger", html)
        self.assertIn('rel="icon"', html)
        self.assertIn('class="brand-mark"', html)
        self.assertIn("Kovaloop Ledger logo", html)
        self.assertIn('id="ledger-state"', html)
        self.assertNotIn('id="wallet-form"', html)
        self.assertNotIn("Agent Wallet</h2>", html)
        self.assertNotIn("Get Or Create Wallet", html)
        self.assertNotIn('id="credit-form"', html)
        self.assertNotIn("Credit Account", html)
        self.assertNotIn('id="onramp-form"', html)
        self.assertNotIn('id="onramp-confirm-form"', html)
        self.assertNotIn("Coinbase Onramp", html)
        self.assertNotIn("Confirm Onramp", html)
        self.assertNotIn('id="settlement-form"', html)
        self.assertNotIn('id="release-button"', html)
        self.assertNotIn('id="refund-button"', html)
        self.assertIn("/ledger/admin/summary", html)
        self.assertNotIn("/onramp/sessions", html)
        self.assertIn("Onramp Sessions", html)
        self.assertIn('{ label: "Email"', html)
        self.assertIn('{ label: "Claimed"', html)
        self.assertIn('{ label: "Claimed At"', html)
        self.assertIn("dashboardClaimedAt", html)
        self.assertIn("dashboardClaimedByEmail", html)
        self.assertIn("Gateway USDC Available", html)
        self.assertIn("Pending Deposits", html)
        self.assertIn('{ label: "Gateway Available"', html)
        self.assertIn('{ label: "Gateway Withdrawable"', html)
        self.assertIn('{ label: "Pending Deposits Atomic"', html)
        self.assertIn('{ label: "Pending Batch Atomic"', html)
        self.assertIn("/ledger/admin/waitlist-applications", html)
        self.assertIn("Waitlist Applications", html)
        self.assertIn('{ label: "Intent"', html)
        self.assertIn("waitlistApplications", html)
        self.assertNotIn('id="debug-claims-form"', html)
        self.assertNotIn('id="debug-token"', html)
        self.assertNotIn('id="debug-claim-agent-ids"', html)
        self.assertNotIn("Dashboard Claims", html)
        self.assertNotIn("Reset Claim Bindings", html)
        self.assertNotIn("/admin/debug/dashboard-claims/reset", html)
        self.assertNotIn('"X-Debug-Token"', html)

    def test_debug_reset_dashboard_claims_requires_token(self) -> None:
        with patch.dict(os.environ, {"LEDGER_DEBUG_ADMIN_TOKEN": "debug-token"}):
            response = self.client.post(
                "/admin/debug/dashboard-claims/reset",
                json={"confirm": "reset-dashboard-claims"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "invalid debug admin token")

    def test_debug_reset_dashboard_claims_clears_claims_only(self) -> None:
        main.get_store().bind_account_wallet(
            agent_id="agent_claimed",
            agent_name="Claimed Agent",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-claimed",
            account_type="EOA",
        )
        main.get_store().claim_dashboard_account(
            agent_id="agent_claimed",
            email="owner@example.com",
            dashboard_email="dashboard@example.com",
        )

        with patch.dict(os.environ, {"LEDGER_DEBUG_ADMIN_TOKEN": "debug-token"}):
            response = self.client.post(
                "/admin/debug/dashboard-claims/reset",
                headers={"X-Debug-Token": "debug-token"},
                json={"confirm": "reset-dashboard-claims"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cleared"], 1)
        account = main.get_store().load().accounts[0]
        self.assertIsNone(account.dashboardClaimedAt)
        self.assertIsNone(account.dashboardClaimedByEmail)
        self.assertEqual(account.walletAddress, "0x1111111111111111111111111111111111111111")
        self.assertEqual(account.circleWalletId, "circle-claimed")

    def test_admin_requires_configured_token(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.get("/admin", follow_redirects=False)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Admin access is not configured")

    def test_admin_rejects_missing_or_invalid_token(self) -> None:
        with patch.dict(os.environ, {"ADMIN_TOKEN": "admin-secret"}):
            missing = self.client.get("/admin", follow_redirects=False)
            invalid = self.client.get("/admin?token=wrong", follow_redirects=False)

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json()["detail"], "Admin authentication required")
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(invalid.json()["detail"], "Admin authentication required")

    def test_admin_token_sets_cookie_for_subsequent_access(self) -> None:
        with patch.dict(os.environ, {"ADMIN_TOKEN": "admin-secret"}):
            login = self.client.get("/admin?token=admin-secret", follow_redirects=False)
            response = self.client.get("/admin", follow_redirects=False)

        self.assertEqual(login.status_code, 307)
        self.assertEqual(login.headers["location"], "/admin")
        self.assertIn("kovaloop_ledger_admin=", login.headers["set-cookie"])
        self.assertIn("HttpOnly", login.headers["set-cookie"])
        self.assertEqual(response.status_code, 200)

    def test_admin_summary_requires_admin_cookie(self) -> None:
        with patch.dict(os.environ, {"ADMIN_TOKEN": "admin-secret"}):
            missing = self.client.get("/ledger/admin/summary")
            self.client.get("/admin?token=admin-secret", follow_redirects=False)
            authorized = self.client.get("/ledger/admin/summary")

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json()["detail"], "Admin authentication required")
        self.assertEqual(authorized.status_code, 200)
        self.assertIn("accounts", authorized.json())

    def test_admin_waitlist_applications_requires_admin_cookie_and_lists_requests(self) -> None:
        self.client.post(
            "/waitlist/applications",
            json={
                "email": "Founder@Example.COM",
                "name": "Founder",
                "company": "Example Labs",
                "intent": "first use case",
                "lang": "zh",
                "page_url": "https://kovaloop.ai/#cta",
                "submitted_at": "2026-06-02T08:00:00.000Z",
            },
        )
        self.client.post(
            "/waitlist/applications",
            json={
                "email": "founder@example.com",
                "name": "Founder",
                "company": "Example Labs",
                "intent": "second use case",
                "lang": "en",
                "page_url": "https://kovaloop.ai/en#cta",
                "submitted_at": "2026-06-02T08:01:00.000Z",
            },
        )

        with patch.dict(os.environ, {"ADMIN_TOKEN": "admin-secret"}):
            missing = self.client.get("/ledger/admin/waitlist-applications")
            self.client.get("/admin?token=admin-secret", follow_redirects=False)
            authorized = self.client.get("/ledger/admin/waitlist-applications?limit=10")

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json()["detail"], "Admin authentication required")
        self.assertEqual(authorized.status_code, 200)
        applications = authorized.json()["applications"]
        self.assertEqual(len(applications), 2)
        self.assertEqual([item["intent"] for item in applications], ["second use case", "first use case"])
        self.assertEqual(applications[0]["email"], "founder@example.com")
        self.assertEqual(applications[0]["lang"], "en")
        self.assertTrue(applications[0]["applicationId"].startswith("waitlist_"))

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
        self.assertIn("googleLoginHref", source)
        self.assertIn('href={githubLoginHref}', source)
        self.assertIn('href={googleLoginHref}', source)
        self.assertIn("mvp.dash.auth.google_button", source)
        self.assertIn("fetch('/auth/session'", source)
        self.assertIn("claimedAgentIds: payload.claimedAgentIds || []", source)
        self.assertIn("Object.prototype.hasOwnProperty.call(opts, 'claimedAgentIds')", source)
        self.assertIn("window.localStorage.removeItem(STORAGE_KEYS.agents)", source)
        self.assertNotIn("window.localStorage.setItem(STORAGE_KEYS.agents", source)
        self.assertNotIn("window.localStorage.getItem(STORAGE_KEYS.agents", source)
        self.assertNotIn("window.localStorage.setItem(STORAGE_KEYS.activeAgent", source)
        self.assertNotIn("window.localStorage.getItem(STORAGE_KEYS.activeAgent", source)
        self.assertIn("fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' })", source)
        self.assertIn(".finally(() => window.location.reload())", source)
        self.assertIn("signOut({ remote: false });", source)
        self.assertIn("const { authChecked, registered, claimed, currentUser } = window.useAppState();", source)
        self.assertIn("if (!registered || !currentUser) return <window.MvpGithubAuthScreen />", source)
        self.assertIn("if (!claimed)    return <window.MvpClaimScreen />", source)
        self.assertIn("window.ClaimForm = ClaimForm", source)
        self.assertIn("fetch('/ledger/claims/candidates'", source)
        self.assertNotIn("claimable-agents?claimed=${claimed}", source)
        self.assertIn("if (response.status === 403) throw new Error('owner_mismatch');", source)
        self.assertIn("setErrorKey(error.message === 'owner_mismatch'", source)
        self.assertNotIn("function ResetLink()", source)
        self.assertNotIn("<ResetLink />", source)
        self.assertNotIn("↻ {t('mvp.dash.reset')}", source)
        self.assertIn("t('mvp.dash.claim.code_label')", source)
        self.assertIn("const canValidate = trimmedCode.length > 0 && status !== 'loading'", source)
        self.assertIn("candidate.claimCode", source)
        self.assertIn("fetch('/ledger/claims'", source)
        self.assertIn("{t('mvp.dash.claim.validate_button')} →", source)
        self.assertIn("pending_settle", source)
        self.assertIn("pending_inbound_chain", source)
        self.assertIn("function InfoTrigger({ tooltipKey", source)
        self.assertIn("window.InfoTrigger = InfoTrigger", source)
        self.assertIn("window.STATUS_CHIP_META = STATUS_CHIP_META", source)
        self.assertIn("'mvp.dash.status.pending_settle.label':    { en: 'SETTLING'", source)
        self.assertIn("'mvp.dash.status.pending_inbound_chain.label': { en: 'CREDITING'", source)
        self.assertIn("'mvp.dash.status.released.label':           { en: 'RELEASED'", source)
        self.assertIn("released: {", source)
        self.assertIn("labelKey: 'mvp.dash.status.released.label'", source)
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
        self.assertIn("fetch(`/ledger/portfolio?ownerEmail=", source)
        self.assertNotIn("fetch(`/ledger/accounts?claimedByEmail=", source)
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

    def test_dashboard_assets_are_not_browser_cached(self) -> None:
        response = self.client.get(
            "/dashboard/assets/dashboard/Shell/MvpAppStateProvider.jsx"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("cache-control"),
            "no-store, no-cache, must-revalidate, max-age=0",
        )

    def test_dashboard_assets_are_served_as_static_files(self) -> None:
        response = self.client.get("/dashboard/assets/dashboard/DashboardApp.jsx")

        self.assertEqual(response.status_code, 200)
        self.assertIn("function DashboardSurface()", response.text)

    def test_management_page_helpers_render_and_call_ledger_api(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node is required to execute ledger page helpers")

        with patch.dict(os.environ, {"ADMIN_TOKEN": "admin-secret"}):
            response = self.client.get("/admin?token=admin-secret", follow_redirects=True)

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
                "if (url === '/ledger/admin/summary') return { ok: true, json: async () => ({ accounts: 1, circleUsdcAvailable: '1.98', gatewayUsdcAvailable: '0.75', pendingDeposits: '2.25', pendingBatch: '0.1', onrampSessions: 1 }) };"
                "if (url === '/ledger/accounts') return { ok: true, json: async () => ({ accounts: [{ agentId: 'agent_buyer', email: 'buyer@example.com', walletAddress: '0x1111111111111111111111111111111111111111', circleUsdcBalance: '1.98', gatewayUsdcAvailable: '0.75', gatewayUsdcTotal: '1.25', gatewayUsdcWithdrawable: '0.5', gatewayUsdcWithdrawing: '0.5', gatewayUsdcPendingDeposits: '2.25', gatewayPendingDepositsAtomic: '2250000', gatewayUsdcPendingBatch: '0.1', gatewayPendingBatchAtomic: '100000', availableAtomic: '5000000' }] }) };"
                "if (url === '/ledger/entries?limit=50') return { ok: true, json: async () => ({ entries: [{ entryId: 'entry_1', entryType: 'credit', agentId: 'agent_buyer' }] }) };"
                "if (url === '/ledger/onramp-sessions?limit=50') return { ok: true, json: async () => ({ onrampSessions: [{ sessionId: 'onramp_1', agentId: 'agentA', paymentAmount: '10.00', status: 'created', onrampUrl: 'https://pay.coinbase.com/buy/select-asset?sessionToken=abc' }] }) };"
                "if (url === '/ledger/admin/waitlist-applications?limit=100') return { ok: true, json: async () => ({ applications: [{ applicationId: 'waitlist_1', email: 'founder@example.com', name: 'Founder', company: 'Example Labs', intent: 'agent payments', lang: 'zh', pageUrl: 'https://kovaloop.ai/#cta', submittedAt: '2026-06-02T08:00:00.000Z', createdAt: '2026-06-02T08:00:01+00:00' }] }) };"
                "return { ok: true, json: async () => ({ ok: true }) };"
                "};"
                "(async () => {"
                "eval(scriptText);"
                "await refreshLedgerState();"
                "process.stdout.write(JSON.stringify({"
                "stateHtml: elements.get('ledger-state').innerHTML,"
                "stateText: elements.get('ledger-state').textContent,"
                "stateCall: fetchCalls.find((call) => call.url === '/ledger/admin/summary'),"
                "waitlistCall: fetchCalls.find((call) => call.url === '/ledger/admin/waitlist-applications?limit=100'),"
                "walletCall: fetchCalls.find((call) => call.url === '/ledger/wallets/get-or-create') || null,"
                "openCall: window.openCalls[0] || null,"
                "listeners: Array.from(listeners.keys())"
                "}));"
                "})().catch((error) => { console.error(error); process.exit(1); });",
            ],
            input=script,
            text=True,
            capture_output=True,
        )

        self.assertEqual(node_result.returncode, 0, node_result.stderr)
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
        self.assertIn("agent_buyer", output["stateHtml"])
        self.assertIn("Email", output["stateHtml"])
        self.assertIn("buyer@example.com", output["stateHtml"])
        self.assertIn("0x1111111111111111111111111111111111111111", output["stateHtml"])
        self.assertIn("onramp_1", output["stateHtml"])
        self.assertIn("entry_1", output["stateHtml"])
        self.assertIn("Waitlist Applications", output["stateHtml"])
        self.assertIn("founder@example.com", output["stateHtml"])
        self.assertIn("agent payments", output["stateHtml"])
        self.assertIn("https://kovaloop.ai/#cta", output["stateHtml"])
        self.assertEqual(output["stateCall"]["method"], "GET")
        self.assertEqual(output["waitlistCall"]["method"], "GET")
        self.assertNotIn("{", output["stateHtml"])
        self.assertNotIn('"accounts"', output["stateHtml"])
        self.assertIsNone(output["walletCall"])
        self.assertNotIn("wallet-form:submit", output["listeners"])
        self.assertNotIn("settlement-form:submit", output["listeners"])
        self.assertNotIn("debug-claims-form:submit", output["listeners"])
        self.assertIsNone(output["openCall"])
