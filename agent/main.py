from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from autonomy import AutonomyController, load_autonomy_config
from chain_mcp_client import ChainMcpClient
from freqtrade_mcp_client import FreqtradeMcpClient, FreqtradeMcpClientError
app = FastAPI(title="OntologyAgent agent")

SYSTEM_PROMPT = (
    "你是一个金融助理。链上相关动作只能通过 chain MCP 工具完成；"
    "中心化交易和量化相关动作只能通过 Freqtrade MCP 工具完成。"
    "在决定是否调用 x402、是否给 Freqtrade 增加 dry-run 资金前，应先查看 guard 状态。"
    "你可以启动、停止或手动驱动资金守门子 Agent，但只有在确有需要时才这么做。"
    "执行任何会改变链上状态、Freqtrade 运行状态或交易状态的动作前，先清晰总结当前状态、"
    "即将执行的动作和影响对象，然后再调用工具。"
)
CHAIN_MCP_URL = os.getenv(
    "CHAIN_MCP_URL",
    os.getenv("EXECUTOR_MCP_URL", "http://chain-mcp:8091/mcp/"),
)
REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("CHAIN_TIMEOUT_SECONDS", os.getenv("EXECUTOR_TIMEOUT_SECONDS", "20"))
)
FREQTRADE_MCP_URL = os.getenv("FREQTRADE_MCP_URL", "http://freqtrade:8090/mcp/")
CHAT_PAGE_PATH = Path(__file__).resolve().parent / "web" / "chat.html"


def get_openai_base_url() -> Optional[str]:
    value = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_ENDPOINT")
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class SignTransferIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    to: str = Field(description="接收地址，例如 0xabc...")
    amountEth: str = Field(
        description="转账金额（ETH 字符串），例如 0.01",
        pattern=r"^\d+(\.\d+)?$",
    )


class ExecutionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    to: str = Field(description="交易目标地址")
    valueEth: str = Field(
        default="0",
        description="附带 ETH 数量（字符串）",
        pattern=r"^\d+(\.\d+)?$",
    )
    data: Optional[str] = Field(default=None, description="可选 calldata（0x...）")


class UserOperationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(description="UserOperation 目标地址")
    maxCostEth: str = Field(
        description="允许最大成本（ETH 字符串）",
        pattern=r"^\d+(\.\d+)?$",
    )
    raw: dict[str, Any] = Field(description="完整 UserOperation 原始字段")


class X402FetchIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl = Field(description="x402 上游 API URL")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = Field(
        default="GET",
        description="请求方法",
    )
    headers: Optional[dict[str, str]] = Field(default=None, description="可选请求头")
    body: Optional[Any] = Field(default=None, description="可选请求体")


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    input: str = Field(min_length=1, description="用户自然语言指令")


class AgentSessionCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    sessionId: str


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    input: str = Field(min_length=1, description="当前轮用户输入")


class AgentChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    sessionId: str
    input: str
    output: str
    messageCount: int


class AgentSessionStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    sessionId: str
    messageCount: int


@dataclass
class AgentSession:
    session_id: str
    messages: list[Any] = field(default_factory=list)


class EmptyIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TradeListIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=200, description="返回记录数")
    offset: int = Field(default=0, ge=0, description="偏移量")


class SyncDryRunWalletIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dryRunWallet: float = Field(ge=0, description="新的 Freqtrade dry-run wallet 资金")


class ForceEnterTradeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pair: str = Field(description="交易对，例如 BTC/USDT")
    side: Literal["long", "short"] = Field(default="long", description="方向")
    stakeAmount: float = Field(description="下单金额")
    price: Optional[float] = Field(default=None, description="limit 单价格")
    orderType: Literal["market", "limit"] = Field(default="market", description="订单类型")
    entryTag: str = Field(default="agent_force_enter", description="可选标签")


class ForceExitTradeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tradeId: str = Field(description="交易 ID，或 all")
    orderType: Literal["market", "limit"] = Field(default="market", description="订单类型")
    amount: Optional[float] = Field(default=None, description="部分平仓数量")


class AgentSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._lock = threading.RLock()

    def create(self) -> AgentSession:
        session = AgentSession(session_id=str(uuid4()))
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> AgentSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session


@lru_cache(maxsize=1)
def get_session_store() -> AgentSessionStore:
    return AgentSessionStore()


@lru_cache(maxsize=1)
def get_chain_mcp_client() -> ChainMcpClient:
    return ChainMcpClient(CHAIN_MCP_URL)


@lru_cache(maxsize=1)
def get_freqtrade_mcp_client() -> FreqtradeMcpClient:
    return FreqtradeMcpClient(FREQTRADE_MCP_URL)


def _unwrap_mcp_result(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    if result.get("isError"):
        raise RuntimeError(f"{tool_name} failed: {result.get('error', result)}")
    return result


async def call_chain_tool(tool_name: str, arguments: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    try:
        result = await get_chain_mcp_client().call_tool(tool_name, arguments or {})
    except Exception as error:
        raise RuntimeError(f"Chain MCP tool failed: {tool_name}: {error}") from error

    return {
        "tool": tool_name,
        "result": _unwrap_mcp_result(tool_name, result),
    }


async def chain_sign_transfer_tool(to: str, amountEth: str) -> dict[str, Any]:
    intent = SignTransferIntent(to=to, amountEth=amountEth)
    return await call_chain_tool(
        "chain_sign_transfer",
        {
            "to": intent.to,
            "amountEth": intent.amountEth,
        },
    )


async def chain_submit_execution_tool(
    to: str,
    valueEth: str = "0",
    data: Optional[str] = None,
) -> dict[str, Any]:
    intent = ExecutionIntent(to=to, valueEth=valueEth, data=data)
    payload: dict[str, Any] = {
        "to": intent.to,
        "valueEth": intent.valueEth,
    }
    if intent.data is not None:
        payload["data"] = intent.data
    return await call_chain_tool("chain_submit_execution", payload)


async def chain_submit_user_operation_tool(
    target: str,
    maxCostEth: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    intent = UserOperationIntent(target=target, maxCostEth=maxCostEth, raw=raw)
    return await call_chain_tool(
        "chain_submit_user_operation",
        {
            "target": intent.target,
            "maxCostEth": intent.maxCostEth,
            "raw": intent.raw,
        },
    )


async def chain_x402_fetch_tool(
    url: str,
    method: str = "GET",
    headers: Optional[dict[str, str]] = None,
    body: Optional[Any] = None,
) -> dict[str, Any]:
    intent = X402FetchIntent(
        url=HttpUrl(url),
        method=method,  # type: ignore[arg-type]
        headers=headers,
        body=body,
    )
    payload: dict[str, Any] = {
        "url": str(intent.url),
        "method": intent.method,
    }
    if intent.headers is not None:
        payload["headers"] = intent.headers
    if intent.body is not None:
        payload["body"] = intent.body
    return await call_chain_tool("chain_x402_fetch", payload)


async def call_freqtrade_tool(tool_name: str, arguments: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    try:
        result = await get_freqtrade_mcp_client().call_tool(tool_name, arguments or {})
    except Exception as error:
        raise RuntimeError(f"Freqtrade MCP tool failed: {tool_name}: {error}") from error

    return {
        "tool": tool_name,
        "result": _unwrap_mcp_result(tool_name, result),
    }


async def get_trading_status_tool() -> dict[str, Any]:
    return await call_freqtrade_tool("get_trading_status")


async def list_strategies_tool() -> dict[str, Any]:
    return await call_freqtrade_tool("list_strategies")


async def get_open_trades_tool(limit: int = 20, offset: int = 0) -> dict[str, Any]:
    return await call_freqtrade_tool("get_open_trades", {"limit": limit, "offset": offset})


async def get_closed_trades_tool(limit: int = 20, offset: int = 0) -> dict[str, Any]:
    return await call_freqtrade_tool("get_closed_trades", {"limit": limit, "offset": offset})


async def get_performance_summary_tool() -> dict[str, Any]:
    return await call_freqtrade_tool("get_performance_summary")


async def get_budget_snapshot_tool() -> dict[str, Any]:
    return await call_freqtrade_tool("get_budget_snapshot")


async def sync_dry_run_wallet_tool(dryRunWallet: float) -> dict[str, Any]:
    intent = SyncDryRunWalletIntent(dryRunWallet=dryRunWallet)
    return await call_freqtrade_tool(
        "sync_dry_run_wallet",
        {"dry_run_wallet": intent.dryRunWallet},
    )


async def start_bot_tool() -> dict[str, Any]:
    return await call_freqtrade_tool("start_bot")


async def stop_bot_tool() -> dict[str, Any]:
    return await call_freqtrade_tool("stop_bot")


async def pause_trading_tool() -> dict[str, Any]:
    return await call_freqtrade_tool("pause_trading")


async def resume_trading_tool() -> dict[str, Any]:
    return await call_freqtrade_tool("resume_trading")


async def force_enter_trade_tool(
    pair: str,
    side: str = "long",
    stakeAmount: float = 0,
    price: Optional[float] = None,
    orderType: str = "market",
    entryTag: str = "agent_force_enter",
) -> dict[str, Any]:
    return await call_freqtrade_tool(
        "force_enter_trade",
        {
            "pair": pair,
            "side": side,
            "stake_amount": stakeAmount,
            "price": price,
            "order_type": orderType,
            "entry_tag": entryTag,
        },
    )


async def force_exit_trade_tool(
    tradeId: str,
    orderType: str = "market",
    amount: Optional[float] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "trade_id": tradeId,
        "order_type": orderType,
    }
    if amount is not None:
        payload["amount"] = amount
    return await call_freqtrade_tool("force_exit_trade", payload)


async def get_guard_status_tool() -> dict[str, Any]:
    return await get_autonomy_controller().status()


async def start_guard_agent_tool() -> dict[str, Any]:
    controller = get_autonomy_controller()
    await controller.start(force=True)
    return await controller.status()


async def stop_guard_agent_tool() -> dict[str, Any]:
    controller = get_autonomy_controller()
    await controller.stop(disable=True)
    return await controller.status()


async def run_guard_tick_tool() -> dict[str, Any]:
    return await get_autonomy_controller().tick()


CHAIN_TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "chain_sign_transfer": {
        "description": "签名 ETH 转账，但不广播。",
        "args_schema": SignTransferIntent,
        "coroutine": chain_sign_transfer_tool,
    },
    "chain_submit_execution": {
        "description": "提交一笔普通链上交易。",
        "args_schema": ExecutionIntent,
        "coroutine": chain_submit_execution_tool,
    },
    "chain_submit_user_operation": {
        "description": "提交一笔 ERC-4337 UserOperation。",
        "args_schema": UserOperationIntent,
        "coroutine": chain_submit_user_operation_tool,
    },
    "chain_x402_fetch": {
        "description": "执行一次 x402 收费资源访问流程。",
        "args_schema": X402FetchIntent,
        "coroutine": chain_x402_fetch_tool,
    },
}


FREQTRADE_TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "get_trading_status": {
        "description": "获取 Freqtrade bot 当前交易状态、运行状态和策略摘要。",
        "args_schema": EmptyIntent,
        "coroutine": get_trading_status_tool,
    },
    "list_strategies": {
        "description": "列出当前仓库内可用的 Freqtrade 策略文件与活动策略。",
        "args_schema": EmptyIntent,
        "coroutine": list_strategies_tool,
    },
    "get_open_trades": {
        "description": "查看当前 open trades。",
        "args_schema": TradeListIntent,
        "coroutine": get_open_trades_tool,
    },
    "get_closed_trades": {
        "description": "查看最近已关闭 trades。",
        "args_schema": TradeListIntent,
        "coroutine": get_closed_trades_tool,
    },
    "get_performance_summary": {
        "description": "查看收益、表现和绩效摘要。",
        "args_schema": EmptyIntent,
        "coroutine": get_performance_summary_tool,
    },
    "get_budget_snapshot": {
        "description": "查看 dry-run 资金、盈亏和预算快照。",
        "args_schema": EmptyIntent,
        "coroutine": get_budget_snapshot_tool,
    },
    "sync_dry_run_wallet": {
        "description": "更新 Freqtrade dry-run wallet 资金；由主 Agent 决定是否调用。",
        "args_schema": SyncDryRunWalletIntent,
        "coroutine": sync_dry_run_wallet_tool,
    },
    "start_bot": {
        "description": "启动 Freqtrade bot。",
        "args_schema": EmptyIntent,
        "coroutine": start_bot_tool,
    },
    "stop_bot": {
        "description": "停止 Freqtrade bot。",
        "args_schema": EmptyIntent,
        "coroutine": stop_bot_tool,
    },
    "pause_trading": {
        "description": "暂停交易。第一阶段语义上等同于 stop bot。",
        "args_schema": EmptyIntent,
        "coroutine": pause_trading_tool,
    },
    "resume_trading": {
        "description": "恢复交易。第一阶段语义上等同于 start bot。",
        "args_schema": EmptyIntent,
        "coroutine": resume_trading_tool,
    },
    "force_enter_trade": {
        "description": "强制开仓一笔 trade。",
        "args_schema": ForceEnterTradeIntent,
        "coroutine": force_enter_trade_tool,
    },
    "force_exit_trade": {
        "description": "强制平掉一笔 trade。",
        "args_schema": ForceExitTradeIntent,
        "coroutine": force_exit_trade_tool,
    },
}


def discover_chain_tools() -> list[StructuredTool]:
    try:
        available_tools = asyncio.run(get_chain_mcp_client().list_tools())
    except Exception as error:
        raise RuntimeError(f"CHAIN_MCP_URL is configured but tools could not be discovered: {error}") from error

    tools: list[StructuredTool] = []
    for tool_name, spec in CHAIN_TOOL_REGISTRY.items():
        if tool_name not in available_tools:
            continue
        tools.append(
            StructuredTool.from_function(
                name=tool_name,
                description=spec["description"],
                args_schema=spec["args_schema"],
                coroutine=spec["coroutine"],
            )
        )
    return tools


def discover_freqtrade_tools() -> list[StructuredTool]:
    try:
        available_tools = asyncio.run(get_freqtrade_mcp_client().list_tools())
    except Exception as error:
        raise RuntimeError(f"FREQTRADE_MCP_URL is configured but tools could not be discovered: {error}") from error

    tools: list[StructuredTool] = []
    for tool_name, spec in FREQTRADE_TOOL_REGISTRY.items():
        if tool_name not in available_tools:
            continue
        tools.append(
            StructuredTool.from_function(
                name=tool_name,
                description=spec["description"],
                args_schema=spec["args_schema"],
                coroutine=spec["coroutine"],
            )
        )
    return tools


def _make_structured_tool(name: str, spec: dict[str, Any]) -> StructuredTool:
    return StructuredTool.from_function(
        name=name,
        description=spec["description"],
        args_schema=spec["args_schema"],
        coroutine=spec["coroutine"],
    )


def build_tools() -> list[StructuredTool]:
    return [
        StructuredTool.from_function(
            name="get_guard_status",
            description="查看资金守门子 Agent 当前状态、阈值、账本和最近建议。",
            args_schema=EmptyIntent,
            coroutine=get_guard_status_tool,
        ),
        StructuredTool.from_function(
            name="start_guard_agent",
            description="启动后台资金守门子 Agent，让它开始周期性检查钱包和 dry-run 风险。",
            args_schema=EmptyIntent,
            coroutine=start_guard_agent_tool,
        ),
        StructuredTool.from_function(
            name="stop_guard_agent",
            description="停止后台资金守门子 Agent。",
            args_schema=EmptyIntent,
            coroutine=stop_guard_agent_tool,
        ),
        StructuredTool.from_function(
            name="run_guard_tick",
            description="立即让资金守门子 Agent 执行一次检查和保护性决策。",
            args_schema=EmptyIntent,
            coroutine=run_guard_tick_tool,
        ),
        *[_make_structured_tool(name, spec) for name, spec in CHAIN_TOOL_REGISTRY.items()],
        *[_make_structured_tool(name, spec) for name, spec in FREQTRADE_TOOL_REGISTRY.items()],
    ]


@lru_cache(maxsize=1)
def get_autonomy_controller() -> AutonomyController:
    return AutonomyController(
        load_autonomy_config(),
        call_chain_tool,
        call_freqtrade_tool,
    )


@lru_cache(maxsize=1)
def get_agent_graph() -> Any:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    model_name = os.getenv("BRAIN_AGENT_MODEL", "gpt-4o-mini")
    llm_kwargs: dict[str, Any] = {
        "model": model_name,
        "temperature": 0,
    }
    base_url = get_openai_base_url()
    if base_url is not None:
        llm_kwargs["base_url"] = base_url
    llm = ChatOpenAI(**llm_kwargs)
    tools = build_tools()
    return create_react_agent(model=llm, tools=tools, prompt=SYSTEM_PROMPT)


def _normalize_message_content(content: Any) -> str:
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
        return "\n".join(parts).strip()

    return str(content)


def _extract_final_output(messages: list[Any]) -> str:
    final_message = None
    for message in reversed(messages):
        if getattr(message, "type", None) == "ai":
            final_message = message
            break

    if final_message is None and messages:
        final_message = messages[-1]

    return _normalize_message_content(
        getattr(final_message, "content", "No response from agent.")
    )


async def _invoke_agent(messages: list[Any]) -> dict[str, Any]:
    try:
        graph = get_agent_graph()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    try:
        return await graph.ainvoke({"messages": messages})
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.on_event("startup")
async def startup_event() -> None:
    await get_autonomy_controller().start()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await get_autonomy_controller().stop(disable=False)


@app.get("/", response_class=HTMLResponse)
def chat_home() -> FileResponse:
    return FileResponse(CHAT_PAGE_PATH)


@app.get("/chat", response_class=HTMLResponse)
def chat_page() -> FileResponse:
    return FileResponse(CHAT_PAGE_PATH)


@app.get("/health")
def health() -> dict[str, Any]:
    chain_tools: list[str] = []
    chain_error: Optional[str] = None
    try:
        chain_tools = asyncio.run(get_chain_mcp_client().list_tools())
    except Exception as error:
        chain_error = str(error)

    freqtrade_tools: list[str] = []
    freqtrade_error: Optional[str] = None
    try:
        freqtrade_tools = asyncio.run(get_freqtrade_mcp_client().list_tools())
    except Exception as error:
        freqtrade_error = str(error)

    autonomy_status: dict[str, Any]
    try:
        autonomy_status = asyncio.run(get_autonomy_controller().status())
    except Exception as error:
        autonomy_status = {
            "enabled": False,
            "running": False,
            "error": str(error),
        }

    return {
        "service": "OntologyAgent-agent",
        "status": "ok",
        "chainMcpUrl": CHAIN_MCP_URL,
        "chainTools": chain_tools,
        "chainError": chain_error,
        "freqtradeMcpUrl": FREQTRADE_MCP_URL,
        "freqtradeTools": freqtrade_tools,
        "freqtradeError": freqtrade_error,
        "autonomy": autonomy_status,
    }


@app.get("/autonomy/status")
async def autonomy_status() -> dict[str, Any]:
    return await get_autonomy_controller().status()


@app.post("/autonomy/start")
async def autonomy_start() -> dict[str, Any]:
    controller = get_autonomy_controller()
    await controller.start(force=True)
    return await controller.status()


@app.post("/autonomy/stop")
async def autonomy_stop() -> dict[str, Any]:
    controller = get_autonomy_controller()
    await controller.stop(disable=True)
    return await controller.status()


@app.post("/autonomy/tick")
async def autonomy_tick() -> dict[str, Any]:
    return await get_autonomy_controller().tick()


@app.post("/agent/sessions")
def create_agent_session() -> AgentSessionCreateResponse:
    session = get_session_store().create()
    return AgentSessionCreateResponse(sessionId=session.session_id)


@app.get("/agent/sessions/{session_id}")
def get_agent_session(session_id: str) -> AgentSessionStateResponse:
    try:
        session = get_session_store().get(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"Unknown agent session: {session_id}") from error

    return AgentSessionStateResponse(sessionId=session.session_id, messageCount=len(session.messages))


@app.post("/agent/sessions/{session_id}/messages")
async def send_agent_session_message(session_id: str, request: AgentChatRequest) -> AgentChatResponse:
    try:
        session = get_session_store().get(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"Unknown agent session: {session_id}") from error

    result = await _invoke_agent([*session.messages, HumanMessage(content=request.input)])
    messages = result.get("messages", [])
    session.messages = list(messages)
    output = _extract_final_output(session.messages)

    return AgentChatResponse(
        sessionId=session.session_id,
        input=request.input,
        output=output,
        messageCount=len(session.messages),
    )


@app.post("/agent/run")
async def run_agent(request: AgentRunRequest) -> dict[str, Any]:
    result = await _invoke_agent([HumanMessage(content=request.input)])
    messages = result.get("messages", [])
    output = _extract_final_output(messages)

    return {
        "ok": True,
        "input": request.input,
        "output": output,
    }
