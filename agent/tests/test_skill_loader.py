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
            skill_dir = tmpdir / "agent-wallet"
            skill_dir.mkdir()
            (skill_dir / "instructions.md").write_text(
                "Create Agent Wallet records through MCP.",
                encoding="utf-8",
            )
            (skill_dir / "skill.json").write_text(
                json.dumps(
                    {
                        "name": "agent-wallet",
                        "description": "Agent Wallet skill",
                        "instructions": "instructions.md",
                        "mcpServers": {
                            "ledger": {
                                "url": "http://ledger:8092/mcp/",
                                "tools": ["agent_wallet_status"],
                            }
                        },
                        "hiddenTools": {"ledger": ["internal_tool"]},
                    }
                ),
                encoding="utf-8",
            )

            catalog = load_skill_catalog(tmpdir)

        self.assertEqual(catalog.server_names(), {"ledger"})
        self.assertEqual(
            catalog.server_url("ledger"),
            "http://ledger:8092/mcp/",
        )
        self.assertEqual(
            catalog.exposed_mcp_tools("ledger"),
            {"agent_wallet_status"},
        )
        self.assertEqual(
            catalog.hidden_mcp_tools_for("ledger"),
            {"internal_tool"},
        )
        self.assertIn("Create Agent Wallet records through MCP.", catalog.instructions_text())

    def test_load_skill_catalog_reads_v2_mcp_servers(self) -> None:
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
                        "enabled": True,
                        "description": "Payment routing skill",
                        "instructions": "instructions.md",
                        "mcpServers": {
                            "ledger": {
                                "url": "http://ledger:8092/mcp/",
                                "tools": ["route_payment_intent"],
                            }
                        },
                        "hiddenTools": {"ledger": ["internal_debug_tool"]},
                    }
                ),
                encoding="utf-8",
            )

            catalog = load_skill_catalog(tmpdir)

        self.assertEqual(catalog.server_names(), {"ledger"})
        self.assertEqual(
            catalog.server_url("ledger"),
            "http://ledger:8092/mcp/",
        )
        self.assertEqual(
            catalog.exposed_mcp_tools("ledger"),
            {"route_payment_intent"},
        )
        self.assertEqual(
            catalog.hidden_mcp_tools_for("ledger"),
            {"internal_debug_tool"},
        )
        self.assertIn("Route payments before settlement.", catalog.instructions_text())

    def test_load_skill_catalog_reads_skill_declared_mcp_url(self) -> None:
        with temporary_test_directory() as tmpdir:
            skill_dir = tmpdir / "chain-wallet"
            skill_dir.mkdir()
            (skill_dir / "skill.json").write_text(
                json.dumps(
                    {
                        "name": "chain-wallet",
                        "enabled": True,
                        "description": "Chain skill",
                        "mcpServers": {
                            "chain": {
                                "url": "http://chain-mcp:8091/mcp/",
                                "tools": ["chain_get_wallet_state"],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            catalog = load_skill_catalog(tmpdir)

        self.assertEqual(catalog.server_url("chain"), "http://chain-mcp:8091/mcp/")
        self.assertEqual(catalog.exposed_mcp_tools("chain"), {"chain_get_wallet_state"})


if __name__ == "__main__":
    unittest.main()
