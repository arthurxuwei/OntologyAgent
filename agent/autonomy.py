from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Optional

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field


ToolInvoker = Callable[[str, Optional[dict[str, Any]]], Awaitable[dict[str, Any]]]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AutonomyConfig:
    enabled: bool
    interval_seconds: float
    state_path: str
    x402_url: str
    x402_method: str
    eth_price_usd: float
    trading_allocation_ratio: float
    min_cash_reserve_ratio: float
    min_net_budget_ratio: float
    max_drawdown_ratio: float
    min_x402_interval_seconds: float
    model_name: str


def load_autonomy_config(env: Optional[dict[str, str]] = None) -> AutonomyConfig:
    source = env if env is not None else os.environ

    def get_bool(name: str, default: bool) -> bool:
        raw = source.get(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def get_text(name: str, default: str) -> str:
        raw = source.get(name)
        if raw is None:
            return default
        normalized = raw.strip()
        return normalized if normalized else default

    return AutonomyConfig(
        enabled=get_bool("AUTONOMY_ENABLED", False),
        interval_seconds=float(source.get("AUTONOMY_INTERVAL_SECONDS", "60")),
        state_path=get_text("AUTONOMY_STATE_PATH", "/app/data/autonomy_state.json"),
        x402_url=get_text("AUTONOMY_X402_URL", "http://x402-seller:8000/x402/demo-resource"),
        x402_method=get_text("AUTONOMY_X402_METHOD", "GET").upper(),
        eth_price_usd=float(source.get("AUTONOMY_ETH_PRICE_USD", "3000")),
        trading_allocation_ratio=float(source.get("AUTONOMY_TRADING_ALLOCATION_RATIO", "0.5")),
        min_cash_reserve_ratio=float(source.get("AUTONOMY_MIN_CASH_RESERVE_RATIO", "0.25")),
        min_net_budget_ratio=float(source.get("AUTONOMY_MIN_NET_BUDGET_RATIO", "0.6")),
        max_drawdown_ratio=float(source.get("AUTONOMY_MAX_DRAWDOWN_RATIO", "0.15")),
        min_x402_interval_seconds=float(source.get("AUTONOMY_MIN_X402_INTERVAL_SECONDS", "1800")),
        model_name=get_text(
            "AUTONOMY_MODEL",
            get_text("BRAIN_AGENT_MODEL", "gpt-4o-mini"),
        ),
    )


class AutonomyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["hold", "start_trading", "stop_trading", "spend_x402", "force_exit_all"]
    reason: str = Field(min_length=1)
    riskLevel: Literal["low", "medium", "high"] = "medium"
    maxSpendAllowed: float = Field(default=0, ge=0)


class BudgetLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initialized: bool = False
    initializedAt: Optional[str] = None
    startingCapitalEth: str = "0"
    startingCapitalUsd: float = 0
    availableBudget: float = 0
    reservedForX402: float = 0
    allocatedToDryRunTrading: float = 0
    x402Spent: float = 0
    dryRunRealizedPnl: float = 0
    dryRunUnrealizedPnl: float = 0
    netBudget: float = 0
    botEnabled: bool = False
    dryRunWalletSynced: bool = False
    lastDecision: Optional[dict[str, Any]] = None
    lastActionResult: Optional[dict[str, Any]] = None
    lastError: Optional[str] = None
    lastTickAt: Optional[str] = None
    lastX402At: Optional[str] = None
    tickCount: int = 0


class AutonomyController:
    def __init__(
        self,
        config: AutonomyConfig,
        chain_tool_invoker: ToolInvoker,
        freqtrade_tool_invoker: ToolInvoker,
    ) -> None:
        self.config = config
        self._chain_tool_invoker = chain_tool_invoker
        self._freqtrade_tool_invoker = freqtrade_tool_invoker
        self._lock: Optional[asyncio.Lock] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._state = self._load_state()
        self._running = False

    async def start(self) -> None:
        if not self.config.enabled or self._task is not None:
            return

        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="agent-autonomy-loop")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        task = self._task
        self._task = None
        if task is not None:
            await task

    async def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "running": self._running,
            "intervalSeconds": self.config.interval_seconds,
            "x402Url": self.config.x402_url,
            "modelName": self.config.model_name,
            "ledger": self._state.model_dump(),
        }

    async def tick(self) -> dict[str, Any]:
        if self._lock is None:
            self._lock = asyncio.Lock()

        async with self._lock:
            chain_state = await self._tool_result(
                self._chain_tool_invoker("chain_get_wallet_state", {}),
            )
            freqtrade_budget = await self._tool_result(
                self._freqtrade_tool_invoker("get_budget_snapshot", {}),
            )

            self._bootstrap_if_needed(chain_state, freqtrade_budget)
            await self._sync_dry_run_wallet_if_needed()

            context = self._build_context(chain_state, freqtrade_budget)
            decision = await self._make_decision(context)
            decision = self._normalize_decision(decision, context)
            action_result = await self._execute_decision(decision)
            self._update_state(context, decision, action_result)
            self._save_state()

            return {
                "context": context,
                "decision": decision.model_dump(),
                "actionResult": action_result,
            }

    async def _run_loop(self) -> None:
        if self._stop_event is None:
            self._stop_event = asyncio.Event()

        self._running = True
        try:
            while not self._stop_event.is_set():
                try:
                    await self.tick()
                except Exception as error:
                    self._state.lastError = str(error)
                    self._save_state()

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.config.interval_seconds,
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            self._running = False

    async def _tool_result(self, awaited: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        payload = await awaited
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"Unexpected MCP tool result shape: {payload}")
        return result

    def _bootstrap_if_needed(self, chain_state: dict[str, Any], freqtrade_budget: dict[str, Any]) -> None:
        if self._state.initialized:
            return

        wallet = chain_state.get("wallet", {})
        if not wallet.get("signerConfigured"):
            raise RuntimeError("Autonomy requires a configured chain signer")

        balance_eth = float(wallet.get("balanceEth", "0"))
        starting_capital_usd = round(balance_eth * self.config.eth_price_usd, 6)
        allocated_to_trading = round(
            starting_capital_usd * self.config.trading_allocation_ratio,
            2,
        )
        reserved_for_x402 = round(starting_capital_usd - allocated_to_trading, 2)

        self._state = BudgetLedger(
            initialized=True,
            initializedAt=utcnow_iso(),
            startingCapitalEth=str(balance_eth),
            startingCapitalUsd=starting_capital_usd,
            availableBudget=starting_capital_usd,
            reservedForX402=reserved_for_x402,
            allocatedToDryRunTrading=allocated_to_trading,
            x402Spent=0,
            dryRunRealizedPnl=0,
            dryRunUnrealizedPnl=0,
            netBudget=starting_capital_usd,
            botEnabled=bool(freqtrade_budget.get("openTradeCount", 0) > 0),
            dryRunWalletSynced=False,
            tickCount=0,
        )

    async def _sync_dry_run_wallet_if_needed(self) -> None:
        if self._state.dryRunWalletSynced:
            return

        await self._tool_result(
            self._freqtrade_tool_invoker(
                "sync_dry_run_wallet",
                {"dry_run_wallet": self._state.allocatedToDryRunTrading},
            ),
        )
        self._state.dryRunWalletSynced = True

    def _build_context(
        self,
        chain_state: dict[str, Any],
        freqtrade_budget: dict[str, Any],
    ) -> dict[str, Any]:
        policy = chain_state.get("policy", {})
        x402_spent = round(int(policy.get("spentTodayUsdcAtomic", "0")) / 1_000_000, 6)
        realized_pnl = round(float(freqtrade_budget.get("realizedPnl", 0)), 6)
        unrealized_pnl = round(float(freqtrade_budget.get("unrealizedPnl", 0)), 6)
        net_budget = round(
            self._state.startingCapitalUsd - x402_spent + realized_pnl + unrealized_pnl,
            6,
        )
        min_cash_reserve = round(
            self._state.startingCapitalUsd * self.config.min_cash_reserve_ratio,
            6,
        )
        min_net_budget = round(
            self._state.startingCapitalUsd * self.config.min_net_budget_ratio,
            6,
        )
        current_drawdown = round(max(0, self._state.startingCapitalUsd - net_budget), 6)
        max_drawdown = round(
            self._state.startingCapitalUsd * self.config.max_drawdown_ratio,
            6,
        )

        can_spend_x402 = (
            self.config.x402_url != ""
            and net_budget >= min_cash_reserve
            and self._x402_cooldown_elapsed()
        )
        should_force_exit = current_drawdown >= max_drawdown
        should_stop = net_budget < min_net_budget
        open_trades = int(freqtrade_budget.get("openTradeCount", 0))

        allowed_actions = ["hold"]
        if should_force_exit and open_trades > 0:
            allowed_actions.append("force_exit_all")
        if self._state.botEnabled:
            allowed_actions.append("stop_trading")
        elif not should_stop:
            allowed_actions.append("start_trading")
        if can_spend_x402 and not should_stop:
            allowed_actions.append("spend_x402")

        return {
            "wallet": chain_state.get("wallet", {}),
            "chain": chain_state.get("chain", {}),
            "policy": policy,
            "freqtrade": freqtrade_budget,
            "budget": {
                "startingCapital": self._state.startingCapitalUsd,
                "availableBudget": net_budget,
                "reservedForX402": self._state.reservedForX402,
                "allocatedToDryRunTrading": self._state.allocatedToDryRunTrading,
                "x402Spent": x402_spent,
                "dryRunRealizedPnl": realized_pnl,
                "dryRunUnrealizedPnl": unrealized_pnl,
                "netBudget": net_budget,
            },
            "risk": {
                "minCashReserve": min_cash_reserve,
                "minNetBudget": min_net_budget,
                "currentDrawdown": current_drawdown,
                "maxDrawdown": max_drawdown,
                "botEnabled": self._state.botEnabled,
                "openTradeCount": open_trades,
                "allowedActions": allowed_actions,
            },
        }

    async def _make_decision(self, context: dict[str, Any]) -> AutonomyDecision:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for autonomy decisions")

        llm = ChatOpenAI(model=self.config.model_name, temperature=0)
        structured_llm = llm.with_structured_output(AutonomyDecision)
        prompt = (
            "你在管理一个会花钱也会赚钱的代理。"
            "只允许在 allowedActions 中选择 action。"
            "优先保护预算与现金储备，再考虑让 dry-run 策略运行和购买 x402 资源。"
            "如果当前回撤过大或预算跌破阈值，应优先 stop_trading 或 force_exit_all。"
            "如果没有足够信息或不应冒险，选择 hold。"
            f"\n\n当前上下文:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
        )
        return await structured_llm.ainvoke(prompt)

    def _normalize_decision(
        self,
        decision: AutonomyDecision,
        context: dict[str, Any],
    ) -> AutonomyDecision:
        allowed_actions = set(context["risk"]["allowedActions"])
        if decision.action in allowed_actions:
            return decision

        return AutonomyDecision(
            action="hold",
            reason=f"Requested action {decision.action} is not currently allowed; holding instead.",
            riskLevel="medium",
            maxSpendAllowed=0,
        )

    async def _execute_decision(self, decision: AutonomyDecision) -> dict[str, Any]:
        if decision.action == "hold":
            return {"action": "hold", "changedState": False}

        if decision.action == "start_trading":
            result = await self._tool_result(self._freqtrade_tool_invoker("start_bot", {}))
            self._state.botEnabled = True
            return {"action": decision.action, "result": result, "changedState": True}

        if decision.action == "stop_trading":
            result = await self._tool_result(self._freqtrade_tool_invoker("stop_bot", {}))
            self._state.botEnabled = False
            return {"action": decision.action, "result": result, "changedState": True}

        if decision.action == "force_exit_all":
            result = await self._tool_result(
                self._freqtrade_tool_invoker(
                    "force_exit_trade",
                    {"trade_id": "all", "order_type": "market"},
                ),
            )
            self._state.botEnabled = False
            return {"action": decision.action, "result": result, "changedState": True}

        if decision.action == "spend_x402":
            result = await self._tool_result(
                self._chain_tool_invoker(
                    "chain_x402_fetch",
                    {"url": self.config.x402_url, "method": self.config.x402_method},
                ),
            )
            self._state.lastX402At = utcnow_iso()
            return {"action": decision.action, "result": result, "changedState": True}

        raise RuntimeError(f"Unsupported autonomy action: {decision.action}")

    def _update_state(
        self,
        context: dict[str, Any],
        decision: AutonomyDecision,
        action_result: dict[str, Any],
    ) -> None:
        budget = context["budget"]
        self._state.availableBudget = float(budget["availableBudget"])
        self._state.x402Spent = float(budget["x402Spent"])
        self._state.dryRunRealizedPnl = float(budget["dryRunRealizedPnl"])
        self._state.dryRunUnrealizedPnl = float(budget["dryRunUnrealizedPnl"])
        self._state.netBudget = float(budget["netBudget"])
        self._state.lastDecision = decision.model_dump()
        self._state.lastActionResult = action_result
        self._state.lastTickAt = utcnow_iso()
        self._state.lastError = None
        self._state.tickCount += 1

    def _x402_cooldown_elapsed(self) -> bool:
        if self._state.lastX402At is None:
            return True

        try:
            previous = datetime.fromisoformat(self._state.lastX402At)
        except ValueError:
            return True

        elapsed = datetime.now(timezone.utc) - previous
        return elapsed.total_seconds() >= self.config.min_x402_interval_seconds

    def _load_state(self) -> BudgetLedger:
        path = Path(self.config.state_path)
        if not path.exists():
            return BudgetLedger()

        try:
            return BudgetLedger.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return BudgetLedger()

    def _save_state(self) -> None:
        path = Path(self.config.state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self._state.model_dump_json(indent=2),
            encoding="utf-8",
        )
