from __future__ import annotations

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


@mcp.tool()
async def get_trading_status() -> dict[str, Any]:
    status = await rest_client.get("/status")
    return summarize_status(status)


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
