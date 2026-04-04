import unittest
from unittest.mock import patch

import main


class MainToolRegistryTests(unittest.TestCase):
    def test_build_tools_includes_guard_management_tools(self) -> None:
        with patch.object(main, "discover_chain_tools", return_value=[]), patch.object(
            main, "discover_freqtrade_tools", return_value=[]
        ):
            tools = main.build_tools()

        tool_names = [tool.name for tool in tools]
        self.assertIn("get_guard_status", tool_names)
        self.assertIn("start_guard_agent", tool_names)
        self.assertIn("stop_guard_agent", tool_names)
        self.assertIn("run_guard_tick", tool_names)


if __name__ == "__main__":
    unittest.main()
