from __future__ import annotations

import importlib.util
import inspect
import json
import os
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP


FREQTRADE_API_URL = os.getenv("FREQTRADE_API_URL", "http://127.0.0.1:8080/api/v1").rstrip("/")
FREQTRADE_USERNAME = os.getenv("FREQTRADE_USERNAME", "freqtrade")
FREQTRADE_PASSWORD = os.getenv("FREQTRADE_PASSWORD", "freqtrade")
FREQTRADE_TIMEOUT_SECONDS = float(os.getenv("FREQTRADE_TIMEOUT_SECONDS", "20"))
FREQTRADE_ALLOW_WRITE_ACTIONS = os.getenv("FREQTRADE_ALLOW_WRITE_ACTIONS", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
FREQTRADE_SIGNAL_DEFAULT_PAIR = os.getenv("FREQTRADE_SIGNAL_DEFAULT_PAIR", "ETH/USDC")
FREQTRADE_SIGNAL_DEFAULT_TIMEFRAME = os.getenv("FREQTRADE_SIGNAL_DEFAULT_TIMEFRAME", "5m")
FREQTRADE_STRATEGY_NAME = os.getenv("FREQTRADE_STRATEGY_NAME", "SimpleAgentStrategy")
FREQTRADE_STRATEGY_PATH = Path(os.getenv("FREQTRADE_STRATEGY_PATH", "/app/strategies"))
FREQTRADE_CONFIG_PATH = Path(os.getenv("FREQTRADE_CONFIG_PATH", "/app/config/config.json"))

mcp = FastMCP(
    "Freqtrade Skill Provider",
    host="0.0.0.0",
    streamable_http_path="/mcp/",
    stateless_http=True,
    json_response=True,
)


class FreqtradeRestError(Exception):
    pass


class FreqtradeRestClient:
    def __init__(self) -> None:
        self.base_url = FREQTRADE_API_URL
        self.auth = httpx.BasicAuth(FREQTRADE_USERNAME, FREQTRADE_PASSWORD)
        self.timeout = FREQTRADE_TIMEOUT_SECONDS

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return await self._request("POST", path, json=payload)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout, auth=self.auth) as client:
            response = await client.request(method, url, **kwargs)

        if response.status_code >= 400:
            raise FreqtradeRestError(
                f"Freqtrade REST request failed: {method} {path} -> "
                f"{response.status_code}: {response.text}"
            )

        if not response.text:
            return {}

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return {"text": response.text}


rest_client = FreqtradeRestClient()


def ensure_write_allowed() -> None:
    if not FREQTRADE_ALLOW_WRITE_ACTIONS:
        raise FreqtradeRestError("Freqtrade write actions are disabled by configuration")


def summarize_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, list):
        return {"summary": "No active trades returned", "raw": payload}

    return {
        "summary": f"Freqtrade returned {len(payload)} active trades",
        "openTradeCount": len(payload),
        "trades": payload,
    }


def summarize_closed_trades(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        trades = payload.get("trades", [])
    else:
        trades = payload

    if not isinstance(trades, list):
        trades = []

    return {
        "summary": f"Freqtrade returned {len(trades)} closed trades",
        "closedTradeCount": len(trades),
        "trades": trades,
    }


def read_config() -> dict[str, Any]:
    return json.loads(FREQTRADE_CONFIG_PATH.read_text(encoding="utf-8"))


def write_config(payload: dict[str, Any]) -> None:
    FREQTRADE_CONFIG_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def extract_first_numeric(payload: Any, keys: list[str]) -> float:
    if not isinstance(payload, dict):
        return 0.0

    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue

    return 0.0


def _load_strategy_instance(resolved_strategy: str) -> Any:
    strategy_file = FREQTRADE_STRATEGY_PATH / f"{resolved_strategy}.py"
    if not strategy_file.exists():
        raise FreqtradeRestError(
            f"Strategy file not found for {resolved_strategy}: {strategy_file}"
        )

    spec = importlib.util.spec_from_file_location(
        f"freqtrade_strategy_{resolved_strategy}",
        strategy_file,
    )
    if spec is None or spec.loader is None:
        raise FreqtradeRestError(
            f"Strategy file could not be loaded for {resolved_strategy}: {strategy_file}"
        )

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise FreqtradeRestError(
            f"Failed to import strategy {resolved_strategy}: {error}"
        ) from error

    strategy_class = getattr(module, resolved_strategy, None)
    if not callable(strategy_class):
        raise FreqtradeRestError(
            f"Strategy class not found for {resolved_strategy}: {strategy_file}"
        )

    try:
        return strategy_class(config=read_config())
    except (FileNotFoundError, TypeError):
        return strategy_class()
    except Exception as error:
        raise FreqtradeRestError(
            f"Failed to initialize strategy {resolved_strategy}: {error}"
        ) from error


def _is_signal_triggered(value: Any) -> bool:
    if value is None:
        return False
    try:
        if value != value:
            return False
    except Exception:
        pass
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "none", "nan"}:
            return False
    return bool(value)


class _SingleRowLocIndexer:
    def __init__(self, dataframe: "_SingleRowDataFrame") -> None:
        self._dataframe = dataframe

    def __setitem__(self, key: Any, value: Any) -> None:
        if not (isinstance(key, tuple) and len(key) == 2 and key[0] == slice(None)):
            raise KeyError(f"Unsupported loc assignment for minimal dataframe: {key!r}")
        column = key[1]
        self._dataframe._row[column] = value


class _SingleRowIlocIndexer:
    def __init__(self, dataframe: "_SingleRowDataFrame") -> None:
        self._dataframe = dataframe

    def __getitem__(self, index: int) -> dict[str, Any]:
        if index not in (-1, 0):
            raise IndexError(index)
        return self._dataframe._row


class _SingleRowDataFrame:
    def __init__(self) -> None:
        self._row = {
            "date": datetime.now(UTC),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1.0,
        }
        self.loc = _SingleRowLocIndexer(self)
        self.iloc = _SingleRowIlocIndexer(self)

    @property
    def empty(self) -> bool:
        return False

    def __setitem__(self, key: str, value: Any) -> None:
        self._row[key] = value

    def __getitem__(self, key: str) -> Any:
        return self._row[key]


def _build_minimal_signal_dataframe() -> _SingleRowDataFrame:
    return _SingleRowDataFrame()


def _evaluate_standard_strategy_signal(
    strategy_instance: Any,
    *,
    pair: str,
    timeframe: str,
) -> tuple[bool, bool] | None:
    populate_indicators = getattr(strategy_instance, "populate_indicators", None)
    populate_entry_trend = getattr(strategy_instance, "populate_entry_trend", None)
    populate_exit_trend = getattr(strategy_instance, "populate_exit_trend", None)
    if not (
        callable(populate_indicators)
        and callable(populate_entry_trend)
        and callable(populate_exit_trend)
    ):
        return None

    metadata = {"pair": pair, "timeframe": timeframe}
    dataframe = _build_minimal_signal_dataframe()
    try:
        dataframe = populate_indicators(dataframe, metadata)
        dataframe = populate_entry_trend(dataframe, metadata)
        dataframe = populate_exit_trend(dataframe, metadata)
    except Exception as error:
        raise FreqtradeRestError(
            f"Failed to evaluate strategy {type(strategy_instance).__name__}: {error}"
        ) from error

    if not hasattr(dataframe, "iloc") or getattr(dataframe, "empty", False):
        return (False, False)

    latest = dataframe.iloc[-1]
    return (
        _is_signal_triggered(latest.get("enter_long")),
        _is_signal_triggered(latest.get("exit_long")),
    )


async def _evaluate_trade_signal_state(
    pair: str,
    strategy: str | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    resolved_strategy = strategy or FREQTRADE_STRATEGY_NAME
    strategy_instance = _load_strategy_instance(resolved_strategy)

    resolved_timeframe = timeframe
    if resolved_timeframe is None and strategy_instance is not None:
        strategy_timeframe = getattr(strategy_instance, "timeframe", None)
        if isinstance(strategy_timeframe, str) and strategy_timeframe:
            resolved_timeframe = strategy_timeframe
    if resolved_timeframe is None:
        resolved_timeframe = FREQTRADE_SIGNAL_DEFAULT_TIMEFRAME

    status = await rest_client.get("/status")
    has_open_position = isinstance(status, list) and any(
        isinstance(trade, dict) and trade.get("pair") == pair for trade in status
    )

    entry_triggered = False
    exit_triggered = False
    evaluate_signal = getattr(strategy_instance, "evaluate_signal", None)
    if callable(evaluate_signal):
        signal_state = evaluate_signal(
            pair=pair,
            timeframe=resolved_timeframe,
            has_open_position=has_open_position,
        )
        if inspect.isawaitable(signal_state):
            signal_state = await signal_state
        if isinstance(signal_state, dict):
            entry_triggered = bool(signal_state.get("entryTriggered"))
            exit_triggered = bool(signal_state.get("exitTriggered"))
    else:
        standard_signal = _evaluate_standard_strategy_signal(
            strategy_instance,
            pair=pair,
            timeframe=resolved_timeframe,
        )
        if standard_signal is None:
            raise FreqtradeRestError(
                f"Strategy {resolved_strategy} cannot be evaluated in V1: "
                "missing evaluate_signal() and standard populate_* methods"
            )
        entry_triggered, exit_triggered = standard_signal

    return {
        "pair": pair,
        "strategy": resolved_strategy,
        "timeframe": resolved_timeframe,
        "hasOpenPosition": has_open_position,
        "entryTriggered": entry_triggered,
        "exitTriggered": exit_triggered,
        "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


@mcp.tool()
async def get_trading_status() -> dict[str, Any]:
    status = await rest_client.get("/status")
    summary = summarize_status(status)
    show_config: dict[str, Any] = {}
    try:
        config_payload = await rest_client.get("/show_config")
        if isinstance(config_payload, dict):
            show_config = config_payload
    except Exception:
        show_config = {}

    return {
        **summary,
        "state": show_config.get("state"),
        "runmode": show_config.get("runmode"),
        "dryRun": show_config.get("dry_run"),
        "exchange": show_config.get("exchange"),
        "strategy": show_config.get("strategy"),
    }


@mcp.tool()
async def evaluate_trade_signal(
    pair: str = FREQTRADE_SIGNAL_DEFAULT_PAIR,
    strategy: str | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    if pair != FREQTRADE_SIGNAL_DEFAULT_PAIR:
        raise FreqtradeRestError(
            f"evaluate_trade_signal only supports {FREQTRADE_SIGNAL_DEFAULT_PAIR} in V1"
        )

    state = await _evaluate_trade_signal_state(pair, strategy=strategy, timeframe=timeframe)
    normalized_pair = state.get("pair", pair)
    normalized_strategy = state.get("strategy")
    normalized_timeframe = state.get("timeframe", timeframe or FREQTRADE_SIGNAL_DEFAULT_TIMEFRAME)
    has_open_position = bool(state.get("hasOpenPosition"))
    entry_triggered = bool(state.get("entryTriggered"))
    exit_triggered = bool(state.get("exitTriggered"))

    signal = "hold"
    confidence = 0.5
    reason = "no actionable entry or exit signal on latest candle"

    if entry_triggered and not has_open_position:
        signal = "buy"
        confidence = 0.7
        reason = "entry conditions satisfied on latest candle and no open position"
    elif exit_triggered and has_open_position:
        signal = "sell"
        confidence = 0.7
        reason = "exit conditions satisfied while position is open"

    return {
        "summary": f"Evaluated trade signal for {normalized_pair}",
        "pair": normalized_pair,
        "strategy": normalized_strategy,
        "timeframe": normalized_timeframe,
        "signal": signal,
        "reason": reason,
        "confidence": confidence,
        "hasOpenPosition": has_open_position,
        "entryTriggered": entry_triggered,
        "exitTriggered": exit_triggered,
        "observedAt": state.get("observedAt"),
    }


@mcp.tool()
async def list_strategies() -> dict[str, Any]:
    strategies = sorted(path.stem for path in FREQTRADE_STRATEGY_PATH.glob("*.py"))
    active_strategy = None
    try:
        config = await rest_client.get("/show_config")
        if isinstance(config, dict):
            active_strategy = config.get("strategy")
    except Exception:
        active_strategy = None

    return {
        "summary": f"Found {len(strategies)} strategies in repository",
        "activeStrategy": active_strategy,
        "strategies": strategies,
    }


@mcp.tool()
async def get_open_trades(limit: int = 20, offset: int = 0) -> dict[str, Any]:
    payload = await rest_client.get("/status")
    summary = summarize_status(payload)
    trades = summary.get("trades", [])
    return {
        **summary,
        "trades": trades[offset : offset + limit],
        "limit": limit,
        "offset": offset,
    }


@mcp.tool()
async def get_closed_trades(limit: int = 20, offset: int = 0) -> dict[str, Any]:
    payload = await rest_client.get("/trades", params={"limit": limit, "offset": offset})
    return {
        **summarize_closed_trades(payload),
        "limit": limit,
        "offset": offset,
    }


@mcp.tool()
async def get_performance_summary() -> dict[str, Any]:
    profit = await rest_client.get("/profit")
    performance = await rest_client.get("/performance")
    balance = await rest_client.get("/balance")
    return {
        "summary": "Combined performance, profit, and balance snapshot",
        "profit": profit,
        "performance": performance,
        "balance": balance,
    }


@mcp.tool()
async def get_budget_snapshot() -> dict[str, Any]:
    config = read_config()
    profit = await rest_client.get("/profit")
    balance = await rest_client.get("/balance")
    status = await rest_client.get("/status")
    show_config = await rest_client.get("/show_config")

    open_trade_count = len(status) if isinstance(status, list) else 0
    realized_pnl = extract_first_numeric(
        profit,
        [
            "profit_closed_coin",
            "profit_closed_abs",
            "profit_closed_fiat",
            "profit_total_abs",
        ],
    )
    unrealized_pnl = extract_first_numeric(
        profit,
        [
            "profit_open_coin",
            "profit_open_abs",
            "profit_open_fiat",
        ],
    )

    return {
        "summary": "Dry-run budget snapshot with config and profit metrics",
        "dryRun": bool(config.get("dry_run", False)),
        "dryRunWallet": float(config.get("dry_run_wallet", 0)),
        "stakeCurrency": config.get("stake_currency"),
        "stakeAmount": float(config.get("stake_amount", 0)),
        "maxOpenTrades": int(config.get("max_open_trades", 0)),
        "activeStrategy": show_config.get("strategy") if isinstance(show_config, dict) else None,
        "initialState": config.get("initial_state"),
        "openTradeCount": open_trade_count,
        "realizedPnl": realized_pnl,
        "unrealizedPnl": unrealized_pnl,
        "profit": profit,
        "balance": balance,
    }


@mcp.tool()
async def sync_dry_run_wallet(dry_run_wallet: float) -> dict[str, Any]:
    ensure_write_allowed()
    if dry_run_wallet < 0:
        raise FreqtradeRestError("dry_run_wallet must be non-negative")

    config = read_config()
    previous_wallet = float(config.get("dry_run_wallet", 0))
    config["dry_run_wallet"] = dry_run_wallet
    write_config(config)

    return {
        "summary": "Updated Freqtrade dry-run wallet in config",
        "previousDryRunWallet": previous_wallet,
        "currentDryRunWallet": dry_run_wallet,
        "configPath": str(FREQTRADE_CONFIG_PATH),
        "restartRecommended": True,
    }


@mcp.tool()
async def start_bot() -> dict[str, Any]:
    ensure_write_allowed()
    payload = await rest_client.post("/start")
    return {
        "summary": "Start bot command sent to Freqtrade",
        "result": payload,
    }


@mcp.tool()
async def stop_bot() -> dict[str, Any]:
    ensure_write_allowed()
    payload = await rest_client.post("/stop")
    return {
        "summary": "Stop bot command sent to Freqtrade",
        "result": payload,
    }


@mcp.tool()
async def pause_trading() -> dict[str, Any]:
    ensure_write_allowed()
    payload = await rest_client.post("/stop")
    return {
        "summary": "Pause trading command sent to Freqtrade (mapped to stop)",
        "result": payload,
    }


@mcp.tool()
async def resume_trading() -> dict[str, Any]:
    ensure_write_allowed()
    payload = await rest_client.post("/start")
    return {
        "summary": "Resume trading command sent to Freqtrade (mapped to start)",
        "result": payload,
    }


@mcp.tool()
async def emit_trade_intent(
    pair: str,
    side: Literal["long", "short"] = "long",
    stake_amount: float = 0,
    order_type: Literal["market", "limit"] = "market",
    price: float | None = None,
    strategy: str | None = None,
    max_slippage_bps: int = 100,
    reason: str = "agent_requested_trade",
) -> dict[str, Any]:
    if side != "long":
        raise FreqtradeRestError("emit_trade_intent only supports long side in V1")
    if stake_amount <= 0:
        raise FreqtradeRestError("stake_amount must be greater than 0")
    if order_type == "limit" and price is None:
        raise FreqtradeRestError("limit orders require a price")
    if order_type == "market" and price is not None:
        raise FreqtradeRestError("market orders do not accept a price")

    return {
        "summary": f"Trade intent prepared for {pair}",
        "intent": {
            "intentId": f"intent-{pair.replace('/', '-').lower()}-{int(stake_amount * 1000)}-{uuid4().hex}",
            "strategy": strategy or FREQTRADE_STRATEGY_NAME,
            "pair": pair,
            "side": side,
            "amount": stake_amount,
            "amountType": "quote",
            "orderType": order_type,
            "limitPrice": price,
            "maxSlippageBps": max_slippage_bps,
            "reason": reason,
        },
    }


@mcp.tool()
async def force_enter_trade(
    pair: str,
    side: Literal["long", "short"] = "long",
    stake_amount: float = 0,
    price: float | None = None,
    order_type: Literal["market", "limit"] = "market",
    entry_tag: str = "agent_force_enter",
) -> dict[str, Any]:
    ensure_write_allowed()
    payload: dict[str, Any] = {
        "pair": pair,
        "side": side,
        "stakeamount": stake_amount,
        "ordertype": order_type,
        "entry_tag": entry_tag,
    }
    if price is not None:
        payload["price"] = price

    result = await rest_client.post("/forceenter", payload)
    return {
        "summary": f"Force enter submitted for {pair}",
        "request": payload,
        "result": result,
    }


@mcp.tool()
async def force_exit_trade(
    trade_id: str,
    order_type: Literal["market", "limit"] = "market",
    amount: float | None = None,
) -> dict[str, Any]:
    ensure_write_allowed()
    payload: dict[str, Any] = {
        "tradeid": trade_id,
        "ordertype": order_type,
    }
    if amount is not None:
        payload["amount"] = amount

    result = await rest_client.post("/forceexit", payload)
    return {
        "summary": f"Force exit submitted for trade {trade_id}",
        "request": payload,
        "result": result,
    }


def build_starlette_app():
    return mcp.streamable_http_app()


def main() -> None:
    app = build_starlette_app()
    uvicorn.run(
        app,
        host=os.getenv("FREQTRADE_MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("FREQTRADE_MCP_PORT", "8090")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
