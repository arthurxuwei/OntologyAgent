from pandas import DataFrame

from freqtrade.strategy import IStrategy


class SimpleAgentStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 21
    minimal_roi = {"0": 0.10}
    stoploss = -0.10

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_fast"] = dataframe["close"].ewm(span=9, adjust=False).mean()
        dataframe["ema_slow"] = dataframe["close"].ewm(span=21, adjust=False).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_tag"] = None

        crossed_above = (dataframe["ema_fast"] > dataframe["ema_slow"]) & (
            dataframe["ema_fast"].shift(1) <= dataframe["ema_slow"].shift(1)
        )

        dataframe.loc[crossed_above, ["enter_long", "enter_tag"]] = (
            1,
            "ema9_cross_above_ema21",
        )
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_tag"] = None

        crossed_below = (dataframe["ema_fast"] < dataframe["ema_slow"]) & (
            dataframe["ema_fast"].shift(1) >= dataframe["ema_slow"].shift(1)
        )

        dataframe.loc[crossed_below, ["exit_long", "exit_tag"]] = (
            1,
            "ema9_cross_below_ema21",
        )
        return dataframe
