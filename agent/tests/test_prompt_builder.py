import unittest

from prompt_builder import build_agent_prompt
from skill_loader import SkillCatalog, SkillDefinition


class PromptBuilderTests(unittest.TestCase):
    def test_build_agent_prompt_uses_minimal_core_and_skill_instructions(self) -> None:
        catalog = SkillCatalog(
            skills=(
                SkillDefinition(
                    name="payment-routing",
                    description="",
                    instructions="Route payments before settlement.",
                ),
            )
        )

        prompt = build_agent_prompt(catalog)

        self.assertIn("You are OntologyAgent.", prompt)
        self.assertIn("Route payments before settlement.", prompt)
        self.assertNotIn("x402 buyer flow", prompt)
        self.assertNotIn("ledger escrow", prompt.lower())


if __name__ == "__main__":
    unittest.main()
