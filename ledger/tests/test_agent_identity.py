import unittest

import pydantic

from models import (
    AgentIdentityAlias,
    AgentProfile,
    CreateAgentProfileRequest,
)


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


if __name__ == "__main__":
    unittest.main()
