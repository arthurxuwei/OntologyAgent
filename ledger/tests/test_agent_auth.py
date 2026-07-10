import base64
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import agent_auth
from utils import generate_agent_id, generate_ulid

_CROCKFORD = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_keypair():
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes_raw()
    return private_key, _b64url(public_raw)


class TestUlid(unittest.TestCase):
    def test_generate_ulid_is_26_crockford_chars(self) -> None:
        value = generate_ulid()
        self.assertEqual(len(value), 26)
        self.assertTrue(set(value).issubset(_CROCKFORD), value)

    def test_generate_ulid_is_unique(self) -> None:
        values = {generate_ulid() for _ in range(1000)}
        self.assertEqual(len(values), 1000)

    def test_generate_agent_id_has_prefix(self) -> None:
        agent_id = generate_agent_id()
        self.assertTrue(agent_id.startswith("kloop_agent_"))
        suffix = agent_id[len("kloop_agent_") :]
        self.assertEqual(len(suffix), 26)
        self.assertTrue(set(suffix).issubset(_CROCKFORD), suffix)


class TestAgentSignature(unittest.TestCase):
    def setUp(self) -> None:
        agent_auth.reset_nonce_cache()

    def test_valid_signature_passes(self) -> None:
        private_key, public_b64 = _make_keypair()
        message = agent_auth.signing_message(
            agent_id="kloop_agent_X", timestamp="2026-06-14T00:00:00Z", nonce="n1", body="{}"
        )
        signature = _b64url(private_key.sign(message.encode("utf-8")))
        self.assertTrue(
            agent_auth.verify_agent_signature(
                public_key_b64=public_b64,
                agent_id="kloop_agent_X",
                timestamp="2026-06-14T00:00:00Z",
                nonce="n1",
                body="{}",
                signature_b64=signature,
            )
        )

    def test_wrong_key_fails(self) -> None:
        signer, _ = _make_keypair()
        _, other_public_b64 = _make_keypair()
        message = agent_auth.signing_message(
            agent_id="kloop_agent_X", timestamp="2026-06-14T00:00:00Z", nonce="n1", body="{}"
        )
        signature = _b64url(signer.sign(message.encode("utf-8")))
        self.assertFalse(
            agent_auth.verify_agent_signature(
                public_key_b64=other_public_b64,
                agent_id="kloop_agent_X",
                timestamp="2026-06-14T00:00:00Z",
                nonce="n1",
                body="{}",
                signature_b64=signature,
            )
        )

    def test_malformed_signature_fails(self) -> None:
        _, public_b64 = _make_keypair()
        self.assertFalse(
            agent_auth.verify_agent_signature(
                public_key_b64=public_b64,
                agent_id="kloop_agent_X",
                timestamp="2026-06-14T00:00:00Z",
                nonce="n1",
                body="{}",
                signature_b64="not-base64-$$$",
            )
        )

    def test_public_key_validation(self) -> None:
        _, public_b64 = _make_keypair()
        self.assertTrue(agent_auth.public_key_is_valid(public_b64))
        self.assertFalse(agent_auth.public_key_is_valid("too-short"))


class TestReplayProtection(unittest.TestCase):
    def setUp(self) -> None:
        agent_auth.reset_nonce_cache()

    def test_fresh_timestamp_and_nonce_accepted(self) -> None:
        self.assertTrue(
            agent_auth.check_timestamp_and_nonce(
                "2026-06-14T00:00:00Z", "nonce-1", now_epoch=1781395200.0
            )
        )

    def test_replayed_nonce_rejected(self) -> None:
        self.assertTrue(
            agent_auth.check_timestamp_and_nonce(
                "2026-06-14T00:00:00Z", "nonce-1", now_epoch=1781395200.0
            )
        )
        self.assertFalse(
            agent_auth.check_timestamp_and_nonce(
                "2026-06-14T00:00:00Z", "nonce-1", now_epoch=1781395201.0
            )
        )

    def test_stale_timestamp_rejected(self) -> None:
        self.assertFalse(
            agent_auth.check_timestamp_and_nonce(
                "2026-06-14T00:00:00Z", "nonce-2", now_epoch=1781395200.0 + 10_000
            )
        )


if __name__ == "__main__":
    unittest.main()
