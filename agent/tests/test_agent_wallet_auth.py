import base64
import hashlib
import hmac
import json
import os
import re
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import agent_wallet_auth


class AgentWalletAuthTests(unittest.TestCase):
    def test_build_github_oauth_state_uses_s256_challenge(self) -> None:
        oauth_state = agent_wallet_auth.build_github_oauth_state()
        digest = hashlib.sha256(oauth_state.code_verifier.encode("ascii")).digest()
        expected_challenge = (
            base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        )

        self.assertEqual(oauth_state.code_challenge, expected_challenge)
        self.assertNotIn("=", oauth_state.code_challenge)

    def test_build_github_login_url_includes_pkce_and_redirect(self) -> None:
        oauth_state = agent_wallet_auth.OAuthState(
            state="state-123",
            code_verifier="verifier-123",
            code_challenge="challenge-123",
        )

        with patch.dict(
            os.environ,
            {
                "GITHUB_CLIENT_ID": "client-123",
                "PUBLIC_BASE_URL": "https://agent.example",
            },
            clear=True,
        ):
            login_url = agent_wallet_auth.build_github_login_url(oauth_state)

        parsed = urlparse(login_url)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "github.com")
        self.assertEqual(parsed.path, "/login/oauth/authorize")
        self.assertEqual(query["client_id"], ["client-123"])
        self.assertEqual(
            query["redirect_uri"], ["https://agent.example/auth/github/callback"]
        )
        self.assertEqual(query["scope"], ["read:user user:email"])
        self.assertEqual(query["state"], ["state-123"])
        self.assertEqual(query["code_challenge"], ["challenge-123"])
        self.assertEqual(query["code_challenge_method"], ["S256"])

    def test_sign_and_verify_session_round_trips_compact_json_payload(self) -> None:
        payload = {"ownerId": "owner_123", "provider": "github"}

        with patch.dict(os.environ, {"AUTH_SESSION_SECRET": "secret"}, clear=True):
            token = agent_wallet_auth.sign_session(payload)
            decoded = agent_wallet_auth.verify_session(token)

        body, signature = token.split(".", 1)
        raw_body = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        expected_signature = base64.urlsafe_b64encode(
            hmac.new(
                b"secret",
                body.encode("ascii"),
                hashlib.sha256,
            ).digest()
        ).decode("ascii").rstrip("=")
        self.assertEqual(json.loads(raw_body.decode("utf-8")), payload)
        self.assertEqual(signature, expected_signature)
        self.assertNotIn("=", signature)
        self.assertIsNone(re.fullmatch(r"[0-9a-f]{64}", signature))
        self.assertEqual(decoded, payload)

    def test_verify_session_returns_none_for_missing_and_bad_values(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(agent_wallet_auth.verify_session(None))

        with patch.dict(os.environ, {"AUTH_SESSION_SECRET": "secret"}, clear=True):
            self.assertIsNone(agent_wallet_auth.verify_session("malformed"))
            self.assertIsNone(agent_wallet_auth.verify_session("body.bad-signature"))


if __name__ == "__main__":
    unittest.main()
