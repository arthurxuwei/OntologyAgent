from pandas import DataFrame

from freqtrade.strategy import IStrategy


class SimpleAgentStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 1
    minimal_roi = {"0": 0.10}
    stoploss = -0.10

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_tag"] = "agent_default"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_tag"] = "agent_default"
        return dataframe
