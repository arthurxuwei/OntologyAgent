from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Optional
from uuid import uuid4

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

from autonomy_models import RuntimeIntent, RuntimeLedger


ToolInvoker = Callable[[str, Optional[dict[str, Any]]], Awaitable[dict[str, Any]]]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _intent_now() -> str:
    return utcnow_iso()


def _new_intent_id() -> str:
    return f"intent-{uuid4()}"


def _round_amount(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def get_openai_base_url() -> Optional[str]:
    value = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_ENDPOINT")
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()

    return str(content)


def _extract_json_object(text: str) -> dict[str, Any]:
    normalized = text.strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?\s*", "", normalized)
        normalized = re.sub(r"\s*```$", "", normalized)

    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", normalized, re.DOTALL)
        if not match:
            raise RuntimeError(
                f"Could not parse GuardDecision JSON from model output: {text}"
            )
        parsed = json.loads(match.group(0))

    if not isinstance(parsed, dict):
        raise RuntimeError(
            f"GuardDecision model output must be a JSON object: {parsed!r}"
        )
    return parsed


@dataclass(frozen=True)
class AutonomyConfig:
    enabled: bool
    interval_seconds: float
    state_path: str
    eth_price_usd: float
    min_wallet_balance_usd: float
    stop_trading_balance_usd: float
    force_exit_balance_usd: float
    max_drawdown_ratio: float
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
        eth_price_usd=float(source.get("AUTONOMY_ETH_PRICE_USD", "3000")),
        min_wallet_balance_usd=float(
            source.get("AUTONOMY_MIN_WALLET_BALANCE_USD", "250")
        ),
        stop_trading_balance_usd=float(
            source.get("AUTONOMY_STOP_TRADING_BALANCE_USD", "150")
        ),
        force_exit_balance_usd=float(
            source.get("AUTONOMY_FORCE_EXIT_BALANCE_USD", "75")
        ),
        max_drawdown_ratio=float(source.get("AUTONOMY_MAX_DRAWDOWN_RATIO", "0.15")),
        model_name=get_text(
            "AUTONOMY_MODEL",
            get_text("BRAIN_AGENT_MODEL", "gpt-4o-mini"),
        ),
    )


class GuardDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["hold", "stop_trading", "force_exit_all", "request_funding"]
    reason: str = Field(min_length=1)
    riskLevel: Literal["low", "medium", "high"] = "medium"
    recommendedFundingUsd: float = Field(default=0, ge=0)


class GuardLedger(RuntimeLedger):
    model_config = ConfigDict(extra="forbid")

    initializedAt: Optional[str] = None
    startingCapitalEth: str = "0"
    startingCapitalUsd: float = 0
    currentWalletBalanceEth: str = "0"
    currentWalletBalanceUsd: float = 0
    dryRunRealizedPnl: float = 0
    dryRunUnrealizedPnl: float = 0
    netWorthEstimate: float = 0
    botEnabled: bool = False
    healthStatus: Literal["healthy", "watch", "critical"] = "healthy"
    lastDecision: Optional[dict[str, Any]] = None
    lastProtectiveAction: Optional[dict[str, Any]] = None
    lastFundingRecommendation: Optional[dict[str, Any]] = None
    lastError: Optional[str] = None
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
        self._enabled = config.enabled
        self._autostart_configured = config.enabled

    async def start(self, *, force: bool = False) -> None:
        if force:
            self._enabled = True

        if not self._enabled or self._task is not None:
            return

        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(), name="agent-wallet-guard-loop"
        )

    async def stop(self, *, disable: bool = True) -> None:
        if disable:
            self._enabled = False
        if self._stop_event is not None:
            self._stop_event.set()
        task = self._task
        self._task = None
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "autostartConfigured": self._autostart_configured,
            "running": self._running,
            "intervalSeconds": self.config.interval_seconds,
            "modelName": self.config.model_name,
            "thresholds": {
                "minWalletBalanceUsd": self.config.min_wallet_balance_usd,
                "stopTradingBalanceUsd": self.config.stop_trading_balance_usd,
                "forceExitBalanceUsd": self.config.force_exit_balance_usd,
                "maxDrawdownRatio": self.config.max_drawdown_ratio,
            },
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

            self._bootstrap_if_needed(chain_state)
            context = self._build_context(chain_state, freqtrade_budget)
            observation = self._build_runtime_observation(chain_state, freqtrade_budget)
            planned_intent = self._plan_intent(observation)
            self._state.latestObservation = observation
            self._state.activeIntents = [planned_intent]
            if planned_intent.intentType != "noop":
                decision = self._decision_from_intent(planned_intent)
            else:
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

    def _bootstrap_if_needed(self, chain_state: dict[str, Any]) -> None:
        if self._state.initialized:
            return

        wallet = chain_state.get("wallet", {})
        if not wallet.get("signerConfigured"):
            raise RuntimeError("Autonomy requires a configured chain signer")

        balance_eth = str(wallet.get("balanceEth", "0"))
        starting_capital_usd = _round_amount(
            float(balance_eth) * self.config.eth_price_usd
        )
        self._state = GuardLedger(
            initialized=True,
            initializedAt=utcnow_iso(),
            startingCapitalEth=balance_eth,
            startingCapitalUsd=starting_capital_usd,
            currentWalletBalanceEth=balance_eth,
            currentWalletBalanceUsd=starting_capital_usd,
            dryRunRealizedPnl=0,
            dryRunUnrealizedPnl=0,
            netWorthEstimate=starting_capital_usd,
            botEnabled=False,
            healthStatus="healthy",
            tickCount=0,
        )

    def _build_context(
        self,
        chain_state: dict[str, Any],
        freqtrade_budget: dict[str, Any],
    ) -> dict[str, Any]:
        wallet = chain_state.get("wallet", {})
        current_wallet_balance_eth = str(wallet.get("balanceEth", "0"))
        current_wallet_balance_usd = _round_amount(
            float(current_wallet_balance_eth) * self.config.eth_price_usd,
        )
        realized_pnl = _round_amount(float(freqtrade_budget.get("realizedPnl", 0)))
        unrealized_pnl = _round_amount(float(freqtrade_budget.get("unrealizedPnl", 0)))
        net_worth_estimate = _round_amount(
            current_wallet_balance_usd + realized_pnl + unrealized_pnl,
        )
        open_trades = int(freqtrade_budget.get("openTradeCount", 0))
        bot_enabled = bool(open_trades > 0 or self._state.botEnabled)
        drawdown_usd = _round_amount(
            max(0.0, self._state.startingCapitalUsd - net_worth_estimate),
        )
        drawdown_ratio = (
            0.0
            if self._state.startingCapitalUsd <= 0
            else _round_amount(drawdown_usd / self._state.startingCapitalUsd)
        )
        health_status = self._compute_health_status(
            current_wallet_balance_usd, drawdown_ratio
        )
        recommended_funding_usd = _round_amount(
            max(0.0, self.config.min_wallet_balance_usd - current_wallet_balance_usd),
        )

        allowed_actions = ["hold"]
        if bot_enabled and (
            current_wallet_balance_usd <= self.config.stop_trading_balance_usd
            or drawdown_ratio >= self.config.max_drawdown_ratio
        ):
            allowed_actions.append("stop_trading")
        if bot_enabled and (
            current_wallet_balance_usd <= self.config.force_exit_balance_usd
            or drawdown_ratio >= self.config.max_drawdown_ratio
        ):
            allowed_actions.append("force_exit_all")
        if recommended_funding_usd > 0:
            allowed_actions.append("request_funding")

        return {
            "wallet": {
                **wallet,
                "balanceUsd": current_wallet_balance_usd,
            },
            "chain": chain_state.get("chain", {}),
            "freqtrade": freqtrade_budget,
            "budget": {
                "startingCapitalEth": self._state.startingCapitalEth,
                "startingCapitalUsd": self._state.startingCapitalUsd,
                "currentWalletBalanceEth": current_wallet_balance_eth,
                "currentWalletBalanceUsd": current_wallet_balance_usd,
                "dryRunRealizedPnl": realized_pnl,
                "dryRunUnrealizedPnl": unrealized_pnl,
                "netWorthEstimate": net_worth_estimate,
            },
            "risk": {
                "healthStatus": health_status,
                "drawdownUsd": drawdown_usd,
                "drawdownRatio": drawdown_ratio,
                "botEnabled": bot_enabled,
                "openTradeCount": open_trades,
                "recommendedFundingUsd": recommended_funding_usd,
                "allowedActions": allowed_actions,
                "thresholds": {
                    "minWalletBalanceUsd": self.config.min_wallet_balance_usd,
                    "stopTradingBalanceUsd": self.config.stop_trading_balance_usd,
                    "forceExitBalanceUsd": self.config.force_exit_balance_usd,
                    "maxDrawdownRatio": self.config.max_drawdown_ratio,
                },
            },
        }

    def _build_runtime_observation(
        self,
        chain_state: dict[str, Any],
        freqtrade_budget: dict[str, Any],
    ) -> dict[str, Any]:
        context = self._build_context(chain_state, freqtrade_budget)
        return {
            "chain": {
                "wallet": context["wallet"],
                "chain": context["chain"],
            },
            "trading": freqtrade_budget,
            "budget": context["budget"],
            "risk": context["risk"],
        }

    def _plan_intent(self, observation: dict[str, Any]) -> RuntimeIntent:
        allowed_actions = set(observation["risk"].get("allowedActions", []))
        bot_enabled = bool(observation["risk"].get("botEnabled"))
        recommended_funding_usd = float(
            observation["risk"].get("recommendedFundingUsd", 0)
        )

        if "force_exit_all" in allowed_actions and bot_enabled:
            return RuntimeIntent(
                intentId=_new_intent_id(),
                intentType="trade",
                action="force_exit_all",
                reason="Open trades are exposed while the runtime is in a critical risk state.",
                confidence=1,
                riskTags=["critical_risk", "protective_action"],
                createdAt=_intent_now(),
                stage="planned",
            )

        if "stop_trading" in allowed_actions and bot_enabled:
            return RuntimeIntent(
                intentId=_new_intent_id(),
                intentType="trade",
                action="stop_trading",
                reason="Trading should stop while the runtime remains in a protective risk state.",
                confidence=1,
                riskTags=["elevated_risk", "protective_action"],
                createdAt=_intent_now(),
                stage="planned",
            )

        if "request_funding" in allowed_actions and recommended_funding_usd > 0:
            return RuntimeIntent(
                intentId=_new_intent_id(),
                intentType="chain",
                action="request_funding",
                parameters={"recommendedFundingUsd": recommended_funding_usd},
                reason="Wallet balance is below the preferred operating range.",
                confidence=1,
                riskTags=["low_balance"],
                createdAt=_intent_now(),
                stage="planned",
            )

        return RuntimeIntent(
            intentId=_new_intent_id(),
            intentType="noop",
            action="hold",
            reason="No protective trade action is currently required.",
            confidence=1,
            createdAt=_intent_now(),
            stage="planned",
        )

    def _decision_from_intent(self, intent: RuntimeIntent) -> GuardDecision:
        return GuardDecision(
            action=intent.action,
            reason=intent.reason or "Runtime intent selected this action.",
            riskLevel=(
                "high"
                if intent.action in {"force_exit_all", "stop_trading"}
                else "medium"
            ),
            recommendedFundingUsd=float(
                intent.parameters.get("recommendedFundingUsd", 0)
            ),
        )

    async def _make_decision(self, context: dict[str, Any]) -> GuardDecision:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for autonomy decisions")

        llm_kwargs: dict[str, Any] = {
            "model": self.config.model_name,
            "temperature": 0,
        }
        base_url = get_openai_base_url()
        if base_url is not None:
            llm_kwargs["base_url"] = base_url
        llm = ChatOpenAI(**llm_kwargs)
        prompt = (
            "你是一个钱包理财子 Agent。"
            "你只关心钱包余额、dry-run 绩效和风险阈值。"
            "只允许在 allowedActions 中选择 action。"
            "你不能主动发起 x402 消费，也不能主动给 Freqtrade 加钱。"
            "当余额或净资产风险过高时，优先执行 stop_trading 或 force_exit_all。"
            "当需要用户补钱时，选择 request_funding。"
            "如果没有保护动作必要，选择 hold。"
            "你必须只返回一个 JSON 对象，不要附加解释、不要使用 markdown 代码块。"
            "JSON 必须严格包含这些字段："
            '{"action":"hold|stop_trading|force_exit_all|request_funding","reason":"...","riskLevel":"low|medium|high","recommendedFundingUsd":0}'
            f"\n\n当前上下文:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
        )
        message = await llm.ainvoke(prompt)
        payload = _extract_json_object(
            _normalize_message_text(getattr(message, "content", ""))
        )
        return GuardDecision.model_validate(payload)

    def _normalize_decision(
        self,
        decision: GuardDecision,
        context: dict[str, Any],
    ) -> GuardDecision:
        allowed_actions = set(context["risk"]["allowedActions"])
        if decision.action in allowed_actions:
            return decision

        return GuardDecision(
            action="hold",
            reason=f"Requested action {decision.action} is not currently allowed; holding instead.",
            riskLevel="medium",
            recommendedFundingUsd=0,
        )

    async def _execute_decision(self, decision: GuardDecision) -> dict[str, Any]:
        if decision.action == "hold":
            return {"action": "hold", "changedState": False}

        if decision.action == "request_funding":
            return {
                "action": decision.action,
                "changedState": False,
                "recommendedFundingUsd": decision.recommendedFundingUsd,
            }

        if decision.action == "stop_trading":
            result = await self._tool_result(
                self._freqtrade_tool_invoker("stop_bot", {})
            )
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

        raise RuntimeError(f"Unsupported autonomy action: {decision.action}")

    def _update_state(
        self,
        context: dict[str, Any],
        decision: GuardDecision,
        action_result: dict[str, Any],
    ) -> None:
        budget = context["budget"]
        risk = context["risk"]
        self._state.currentWalletBalanceEth = str(budget["currentWalletBalanceEth"])
        self._state.currentWalletBalanceUsd = float(budget["currentWalletBalanceUsd"])
        self._state.dryRunRealizedPnl = float(budget["dryRunRealizedPnl"])
        self._state.dryRunUnrealizedPnl = float(budget["dryRunUnrealizedPnl"])
        self._state.netWorthEstimate = float(budget["netWorthEstimate"])
        self._state.botEnabled = bool(risk["botEnabled"])
        self._state.healthStatus = risk["healthStatus"]
        self._state.lastDecision = decision.model_dump()
        self._state.lastTickAt = utcnow_iso()
        self._state.lastError = None
        self._state.tickCount += 1

        if decision.action in {"stop_trading", "force_exit_all"}:
            self._state.lastProtectiveAction = {
                "action": decision.action,
                "result": action_result,
                "at": self._state.lastTickAt,
            }

        if decision.action == "request_funding":
            self._state.lastFundingRecommendation = {
                "action": decision.action,
                "recommendedFundingUsd": decision.recommendedFundingUsd,
                "reason": decision.reason,
                "at": self._state.lastTickAt,
            }

    def _compute_health_status(
        self,
        current_wallet_balance_usd: float,
        drawdown_ratio: float,
    ) -> Literal["healthy", "watch", "critical"]:
        if (
            current_wallet_balance_usd <= self.config.force_exit_balance_usd
            or drawdown_ratio >= self.config.max_drawdown_ratio
        ):
            return "critical"

        if current_wallet_balance_usd <= self.config.min_wallet_balance_usd:
            return "watch"

        return "healthy"

    def _load_state(self) -> GuardLedger:
        path = Path(self.config.state_path)
        if not path.exists():
            return GuardLedger()

        try:
            return GuardLedger.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return GuardLedger()

    def _save_state(self) -> None:
        path = Path(self.config.state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self._state.model_dump_json(indent=2),
            encoding="utf-8",
        )
