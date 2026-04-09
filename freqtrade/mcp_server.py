from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

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
