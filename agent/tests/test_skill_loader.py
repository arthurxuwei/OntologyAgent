import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from skill_loader import load_skill_catalog


@contextmanager
def temporary_test_directory():
    test_dir = Path.cwd() / f".test-skill-loader-{uuid.uuid4().hex}"
    test_dir.mkdir()
    try:
        yield test_dir
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


class SkillLoaderTests(unittest.TestCase):
    def test_load_skill_catalog_reads_manifest_and_instructions(self) -> None:
        with temporary_test_directory() as tmpdir:
            skill_dir = tmpdir / "payment-routing"
            skill_dir.mkdir()
            (skill_dir / "instructions.md").write_text(
                "Route payments before settlement.",
                encoding="utf-8",
            )
            (skill_dir / "skill.json").write_text(
                json.dumps(
                    {
                        "name": "payment-routing",
                        "description": "Payment routing skill",
                        "instructions": "instructions.md",
                    }
                ),
                encoding="utf-8",
            )

            catalog = load_skill_catalog(tmpdir)

        self.assertEqual([skill.name for skill in catalog.skills], ["payment-routing"])
        self.assertIn("Route payments before settlement.", catalog.instructions_text())

    def test_load_skill_catalog_ignores_disabled_skills(self) -> None:
        with temporary_test_directory() as tmpdir:
            skill_dir = tmpdir / "disabled"
            skill_dir.mkdir()
            (skill_dir / "skill.json").write_text(
                json.dumps(
                    {
                        "name": "disabled",
                        "enabled": False,
                        "description": "Disabled skill",
                    }
                ),
                encoding="utf-8",
            )

            catalog = load_skill_catalog(tmpdir)

        self.assertEqual(catalog.skills, ())


if __name__ == "__main__":
    unittest.main()
