import unittest
import uuid
from pathlib import Path

import pydantic

from models import (
    AgentIdentityAlias,
    AgentProfile,
    CreateAgentProfileRequest,
)
from store import OffchainLedgerStore
from utils import now_iso


class TestIdentityModels(unittest.TestCase):
    def test_agent_profile_defaults(self) -> None:
        profile = AgentProfile(
            agentId="kloop_agent_X",
            agentName="OntologyAgent",
            ownerEmail="owner@example.com",
            credentialPublicKey="pk",
            createdAt="2026-06-14T00:00:00Z",
            updatedAt="2026-06-14T00:00:00Z",
        )
        self.assertEqual(profile.schemaVersion, 1)
        self.assertEqual(profile.credentialStatus, "active")
        self.assertIsNone(profile.description)

    def test_alias_record_requires_fields(self) -> None:
        alias = AgentIdentityAlias(
            provider="eigenflux",
            externalId="312586087945994240",
            agentId="kloop_agent_X",
            createdAt="2026-06-14T00:00:00Z",
        )
        self.assertEqual(alias.provider, "eigenflux")

    def test_create_request_rejects_unknown_field(self) -> None:
        with self.assertRaises(pydantic.ValidationError):
            CreateAgentProfileRequest(
                agentName="A",
                ownerEmail="o@example.com",
                credentialPublicKey="pk",
                bogus="x",
            )


class TestProfileStore(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path(__file__).resolve().parents[2] / ".codex-tmp"
        temp_root.mkdir(exist_ok=True)
        self.temp_dir = temp_root / f"profile-store-{uuid.uuid4().hex}"
        self.temp_dir.mkdir()
        self.store = OffchainLedgerStore(str(self.temp_dir / "ledger.sqlite3"))

    def _profile(self, agent_id: str, email: str = "owner@example.com") -> AgentProfile:
        stamp = now_iso()
        return AgentProfile(
            agentId=agent_id,
            agentName="OntologyAgent",
            ownerEmail=email,
            credentialPublicKey="pk",
            createdAt=stamp,
            updatedAt=stamp,
        )

    def _alias(self, agent_id: str, external_id: str = "312586087945994240") -> AgentIdentityAlias:
        return AgentIdentityAlias(
            provider="eigenflux",
            externalId=external_id,
            agentId=agent_id,
            createdAt=now_iso(),
        )

    def test_create_and_get_profile(self) -> None:
        saved = self.store.create_agent_profile(
            profile=self._profile("kloop_agent_A"), aliases=[]
        )
        self.assertEqual(saved.agentId, "kloop_agent_A")
        fetched = self.store.get_agent_profile("kloop_agent_A")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.ownerEmail, "owner@example.com")

    def test_get_missing_profile_returns_none(self) -> None:
        self.assertIsNone(self.store.get_agent_profile("nope"))

    def test_alias_resolves_to_canonical_id(self) -> None:
        self.store.create_agent_profile(
            profile=self._profile("kloop_agent_A"),
            aliases=[self._alias("kloop_agent_A")],
        )
        resolved = self.store.get_profile_id_by_alias("eigenflux", "312586087945994240")
        self.assertEqual(resolved, "kloop_agent_A")

    def test_duplicate_alias_rejected(self) -> None:
        self.store.create_agent_profile(
            profile=self._profile("kloop_agent_A"),
            aliases=[self._alias("kloop_agent_A")],
        )
        with self.assertRaises(ValueError):
            self.store.create_agent_profile(
                profile=self._profile("kloop_agent_B"),
                aliases=[self._alias("kloop_agent_B")],
            )

    def test_rotate_credential_updates_key(self) -> None:
        self.store.create_agent_profile(profile=self._profile("kloop_agent_A"), aliases=[])
        updated = self.store.update_agent_credential("kloop_agent_A", "pk2")
        self.assertEqual(updated.credentialPublicKey, "pk2")
        self.assertEqual(self.store.get_agent_profile("kloop_agent_A").credentialPublicKey, "pk2")

    def test_rotate_missing_profile_raises(self) -> None:
        with self.assertRaises(LookupError):
            self.store.update_agent_credential("nope", "pk2")

    def test_update_description(self) -> None:
        self.store.create_agent_profile(profile=self._profile("kloop_agent_A"), aliases=[])
        updated = self.store.update_agent_description("kloop_agent_A", "new bio")
        self.assertEqual(updated.description, "new bio")


if __name__ == "__main__":
    unittest.main()
