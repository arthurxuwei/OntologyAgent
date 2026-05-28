import unittest
from pathlib import Path

from prompt_builder import build_agent_prompt
from skill_loader import SkillCatalog, SkillDefinition, load_skill_catalog


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

    def test_empty_skill_catalog_keeps_prompt_minimal(self) -> None:
        catalog = load_skill_catalog(Path(__file__).resolve().parents[1] / "skills")

        prompt = build_agent_prompt(catalog)

        self.assertIn("You are OntologyAgent.", prompt)
        self.assertNotIn("Available skills:", prompt)


if __name__ == "__main__":
    unittest.main()
