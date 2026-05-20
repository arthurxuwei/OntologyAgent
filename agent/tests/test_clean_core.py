import ast
from pathlib import Path
import unittest


BANNED_IMPORTS = {
    "agent_wallet_auth",
    "agent_wallet_state",
    "agent_wallet_flows",
    "chain_wallet_tools",
    "payment_ledger_tools",
    "payment_router",
    "ledger_client",
    "autonomy",
}


def agent_root() -> Path:
    local_root = Path("agent")
    if (local_root / "main.py").exists():
        return local_root
    return Path(__file__).resolve().parents[1]


class CleanCoreTests(unittest.TestCase):
    def test_agent_core_does_not_import_domain_modules(self) -> None:
        root = agent_root()
        core_files = [
            root / "main.py",
            root / "prompt_builder.py",
            root / "rest_tool_registry.py",
            root / "skill_loader.py",
        ]
        violations = []
        for path in core_files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] in BANNED_IMPORTS:
                            violations.append(f"{path}:{alias.name}")
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.split(".")[0] in BANNED_IMPORTS:
                        violations.append(f"{path}:{node.module}")

        self.assertEqual(violations, [])

    def test_agent_core_has_no_agent_wallet_routes(self) -> None:
        source = (agent_root() / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("/agent-wallet/", source)
        self.assertNotIn("agent_wallet_", source)


if __name__ == "__main__":
    unittest.main()
