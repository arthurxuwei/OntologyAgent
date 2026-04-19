import importlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path

import pandas as pd


STRATEGY_FILE = (
    Path(__file__).resolve().parents[1] / "strategies" / "SimpleAgentStrategy.py"
)


class _StubIStrategy:
    pass


def load_strategy_class() -> type:
    missing = object()
    original_strategy_module = sys.modules.get("freqtrade.strategy", missing)
    injected_stub = False

    try:
        importlib.import_module("freqtrade.strategy")
    except ModuleNotFoundError as error:
        if error.name != "freqtrade.strategy":
            raise
        strategy_module = types.ModuleType("freqtrade.strategy")
        strategy_module.IStrategy = _StubIStrategy
        sys.modules["freqtrade.strategy"] = strategy_module
        injected_stub = True

    try:
        module_name = "_test_simple_agent_strategy_module"
        spec = importlib.util.spec_from_file_location(module_name, STRATEGY_FILE)
        module = importlib.util.module_from_spec(spec)

        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        return module.SimpleAgentStrategy
    finally:
        if injected_stub:
            if original_strategy_module is missing:
                sys.modules.pop("freqtrade.strategy", None)
            else:
                sys.modules["freqtrade.strategy"] = original_strategy_module


def make_dataframe(closes: list[int]) -> pd.DataFrame:
    return pd.DataFrame({"close": closes})


class SimpleAgentStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_strategy_module = sys.modules.get("freqtrade.strategy")
        self.strategy_class = load_strategy_class()
        self.strategy = self.strategy_class()

    def tearDown(self) -> None:
        if self.original_strategy_module is None:
            sys.modules.pop("freqtrade.strategy", None)
        else:
            sys.modules["freqtrade.strategy"] = self.original_strategy_module

    def test_load_strategy_class_does_not_leave_stubbed_dependency_loaded(self) -> None:
        if self.original_strategy_module is None:
            self.assertNotIn("freqtrade.strategy", sys.modules)
        else:
            self.assertIs(sys.modules.get("freqtrade.strategy"), self.original_strategy_module)

    def test_bullish_crossover_sets_enter_long_and_tag_on_final_row_only(self) -> None:
        dataframe = make_dataframe([10] * 25 + [9, 8, 7, 6, 5, 20])

        dataframe = self.strategy.populate_indicators(dataframe, {})
        dataframe = self.strategy.populate_entry_trend(dataframe, {})

        self.assertTrue(dataframe.iloc[:-1]["enter_long"].eq(0).all())
        self.assertTrue(dataframe.iloc[:-1]["enter_tag"].isna().all())
        self.assertEqual(dataframe.iloc[-1]["enter_long"], 1)
        self.assertEqual(dataframe.iloc[-1]["enter_tag"], "ema9_cross_above_ema21")

    def test_bearish_crossover_sets_exit_long_and_tag_on_final_row_only(self) -> None:
        dataframe = make_dataframe([20] * 25 + [21, 22, 23, 24, 25, 5])

        dataframe = self.strategy.populate_indicators(dataframe, {})
        dataframe = self.strategy.populate_exit_trend(dataframe, {})

        self.assertTrue(dataframe.iloc[:-1]["exit_long"].eq(0).all())
        self.assertTrue(dataframe.iloc[:-1]["exit_tag"].isna().all())
        self.assertEqual(dataframe.iloc[-1]["exit_long"], 1)
        self.assertEqual(dataframe.iloc[-1]["exit_tag"], "ema9_cross_below_ema21")

    def test_flat_data_leaves_entry_and_exit_signals_unset(self) -> None:
        dataframe = make_dataframe([10] * 31)

        dataframe = self.strategy.populate_indicators(dataframe, {})
        dataframe = self.strategy.populate_entry_trend(dataframe, {})
        dataframe = self.strategy.populate_exit_trend(dataframe, {})

        self.assertTrue(dataframe["enter_long"].eq(0).all())
        self.assertTrue(dataframe["exit_long"].eq(0).all())


if __name__ == "__main__":
    unittest.main()
