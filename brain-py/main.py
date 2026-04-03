from __future__ import annotations

import asyncio
import json
import os
from functools import lru_cache
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from executor_client import ExecutorClient, ExecutorClientError
from freqtrade_mcp_client import FreqtradeMcpClient, FreqtradeMcpClientError
from x402_seller import (
    BASE_SEPOLIA_NETWORK,
    X402SellerConfig,
    X402SellerError,
    X402SellerService,
)

app = FastAPI(title="OntologyAgent brain-py")

SYSTEM_PROMPT = (
    "你是一个金融助理。链上相关动作只能通过 TS 执行器工具完成；"
    "中心化交易和量化相关动作只能通过 Freqtrade MCP 工具完成。"
    "执行任何会改变 Freqtrade 运行状态或交易状态的动作前，先清晰总结当前状态、"
    "即将执行的动作和影响对象，然后再调用工具。"
)
EXECUTOR_BASE_URL = os.getenv("EXECUTOR_BASE_URL", "http://executor-ts:3000")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("EXECUTOR_TIMEOUT_SECONDS", "20"))
FREQTRADE_MCP_URL = os.getenv("FREQTRADE_MCP_URL", "http://freqtrade:8090/mcp/")


class SignTransferIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    to: str = Field(description="接收地址，例如 0xabc...")
    amountEth: str = Field(
        description="转账金额（ETH 字符串），例如 0.01",
        pattern=r"^\d+(\.\d+)?$",
    )


class SwapTxIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    to: str = Field(description="swap 目标合约地址")
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

    apiUrl: HttpUrl = Field(description="x402 上游 API URL")
    apiMethod: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = Field(
        default="GET",
        description="请求方法",
    )
    apiHeaders: Optional[dict[str, str]] = Field(default=None, description="可选请求头")
    apiBody: Optional[Any] = Field(default=None, description="可选请求体")
    swapTx: Optional[SwapTxIntent] = Field(default=None, description="可选后续链上交易参数")
    userOperation: Optional[UserOperationIntent] = Field(
        default=None,
        description="可选 ERC-4337 UserOperation",
    )


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    input: str = Field(min_length=1, description="用户自然语言指令")


class EmptyIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TradeListIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=200, description="返回记录数")
    offset: int = Field(default=0, ge=0, description="偏移量")


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


@lru_cache(maxsize=1)
def get_executor_client() -> ExecutorClient:
    return ExecutorClient(
        base_url=EXECUTOR_BASE_URL,
        timeout_seconds=REQUEST_TIMEOUT_SECONDS,
    )


@lru_cache(maxsize=1)
def get_freqtrade_mcp_client() -> FreqtradeMcpClient:
    return FreqtradeMcpClient(FREQTRADE_MCP_URL)


@lru_cache(maxsize=1)
def get_x402_seller_service() -> X402SellerService:
    pay_to = os.getenv("X402_PAY_TO")
    if not pay_to:
        raise RuntimeError("X402_PAY_TO is not configured")

    return X402SellerService(
        X402SellerConfig(
            pay_to=pay_to,
            facilitator_url=os.getenv("X402_FACILITATOR_URL", "https://x402.org/facilitator"),
            price=os.getenv("X402_PRICE", "$0.01"),
            network=os.getenv("X402_NETWORK", BASE_SEPOLIA_NETWORK),
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )
    )


def sign_transfer_tool(to: str, amountEth: str) -> dict[str, Any]:
    intent = SignTransferIntent(to=to, amountEth=amountEth)
    return get_executor_client().sign_transfer(
        to=intent.to,
        amount_eth=intent.amountEth,
    )


def execute_swap_tool(
    apiUrl: str,
    apiMethod: str = "GET",
    apiHeaders: Optional[dict[str, str]] = None,
    apiBody: Optional[Any] = None,
    swapTx: Optional[dict[str, Any]] = None,
    userOperation: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    intent = X402FetchIntent(
        apiUrl=apiUrl,
        apiMethod=apiMethod,
        apiHeaders=apiHeaders,
        apiBody=apiBody,
        swapTx=swapTx,
        userOperation=userOperation,
    )
    client = get_executor_client()

    payment_result = client.x402_fetch(
        url=str(intent.apiUrl),
        method=intent.apiMethod,
        headers=intent.apiHeaders,
        body=intent.apiBody,
    )

    execution_result = None
    if intent.swapTx is not None:
        execution_result = client.submit_execution(
            to=intent.swapTx.to,
            value_eth=intent.swapTx.valueEth,
            data=intent.swapTx.data,
        )

    user_operation_result = None
    if intent.userOperation is not None:
        user_operation_result = client.submit_user_operation(
            target=intent.userOperation.target,
            max_cost_eth=intent.userOperation.maxCostEth,
            raw=intent.userOperation.raw,
        )

    return {
        "payment": payment_result,
        "execution": execution_result,
        "userOperation": user_operation_result,
    }


async def call_freqtrade_tool(tool_name: str, arguments: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    try:
        result = await get_freqtrade_mcp_client().call_tool(tool_name, arguments or {})
    except Exception as error:
        raise RuntimeError(f"Freqtrade MCP tool failed: {tool_name}: {error}") from error

    return {
        "tool": tool_name,
        "result": result,
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


def build_tools() -> list[StructuredTool]:
    sign_transfer = StructuredTool.from_function(
        name="sign_transfer",
        description="签名 ETH 转账（不广播），仅用于资金移动相关需求。",
        func=sign_transfer_tool,
        args_schema=SignTransferIntent,
    )
    execute_swap = StructuredTool.from_function(
        name="execute_swap",
        description=(
            "访问 x402 收费资源，并可选在付费成功后提交链上交易或 ERC-4337 "
            "UserOperation。"
        ),
        func=execute_swap_tool,
        args_schema=X402FetchIntent,
    )
    return [sign_transfer, execute_swap, *discover_freqtrade_tools()]


@lru_cache(maxsize=1)
def get_agent_graph() -> Any:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    model_name = os.getenv("BRAIN_AGENT_MODEL", "gpt-4o-mini")
    llm = ChatOpenAI(model=model_name, temperature=0)
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


@app.get("/health")
def health() -> dict[str, Any]:
    freqtrade_tools: list[str] = []
    freqtrade_error: Optional[str] = None
    try:
        freqtrade_tools = asyncio.run(get_freqtrade_mcp_client().list_tools())
    except Exception as error:
        freqtrade_error = str(error)

    return {
        "service": "OntologyAgent-brain-py",
        "status": "ok",
        "x402Network": os.getenv("X402_NETWORK", BASE_SEPOLIA_NETWORK),
        "x402PayToConfigured": bool(os.getenv("X402_PAY_TO")),
        "freqtradeMcpUrl": FREQTRADE_MCP_URL,
        "freqtradeTools": freqtrade_tools,
        "freqtradeError": freqtrade_error,
    }


@app.get("/x402/demo-resource")
async def x402_demo_resource(request: Request):
    try:
        seller = get_x402_seller_service()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    try:
        authorization = await seller.authorize_or_challenge(request)
    except X402SellerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    if isinstance(authorization, JSONResponse):
        return authorization

    return seller.build_success_response(
        {
            "ok": True,
            "resource": "demo-x402-resource",
            "network": os.getenv("X402_NETWORK", BASE_SEPOLIA_NETWORK),
            "quote": {
                "tokenIn": "ETH",
                "tokenOut": "USDC",
                "price": os.getenv("X402_PRICE", "$0.01"),
            },
        },
        authorization,
    )


@app.post("/x402/mock-facilitator/verify")
async def mock_x402_facilitator_verify(request: Request) -> dict[str, Any]:
    body = await request.json()
    payment_payload = body.get("paymentPayload")
    payment_requirements = body.get("paymentRequirements")

    if not isinstance(payment_payload, dict) or not isinstance(payment_requirements, dict):
        raise HTTPException(status_code=400, detail="missing paymentPayload or paymentRequirements")

    accepted = payment_payload.get("accepted", {})
    if accepted.get("network") != payment_requirements.get("network"):
        return {
            "isValid": False,
            "invalidReason": "NETWORK_MISMATCH",
            "invalidMessage": "payment network does not match requirements",
        }

    payer = (
        payment_payload.get("payload", {})
        .get("authorization", {})
        .get("from")
    )
    return {
        "isValid": True,
        "payer": payer,
    }


@app.post("/x402/mock-facilitator/settle")
async def mock_x402_facilitator_settle(request: Request) -> dict[str, Any]:
    body = await request.json()
    payment_payload = body.get("paymentPayload")
    payment_requirements = body.get("paymentRequirements")

    if not isinstance(payment_payload, dict) or not isinstance(payment_requirements, dict):
        raise HTTPException(status_code=400, detail="missing paymentPayload or paymentRequirements")

    payer = (
        payment_payload.get("payload", {})
        .get("authorization", {})
        .get("from")
    )
    return {
        "success": True,
        "transaction": f"0xmock_x402_settlement_{abs(hash(json_dump_sorted(body))) % 10**12:x}",
        "network": payment_requirements.get("network", BASE_SEPOLIA_NETWORK),
        "payer": payer,
    }


@app.post("/agent/run")
def run_agent(request: AgentRunRequest) -> dict[str, Any]:
    try:
        graph = get_agent_graph()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    try:
        result = graph.invoke({"messages": [{"role": "user", "content": request.input}]})
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    messages = result.get("messages", [])
    final_message = None
    for message in reversed(messages):
        if getattr(message, "type", None) == "ai":
            final_message = message
            break

    if final_message is None and messages:
        final_message = messages[-1]

    output = _normalize_message_content(
        getattr(final_message, "content", "No response from agent.")
    )

    return {
        "ok": True,
        "input": request.input,
        "output": output,
    }


def json_dump_sorted(value: Any) -> str:
    return json.dumps(value, sort_keys=True)
