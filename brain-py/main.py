from __future__ import annotations

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
from x402_seller import (
    BASE_SEPOLIA_NETWORK,
    X402SellerConfig,
    X402SellerError,
    X402SellerService,
)

app = FastAPI(title="OntologyAgent brain-py")

SYSTEM_PROMPT = "你是一个金融助理，只能通过调用 TS 执行器接口来移动资金。"
EXECUTOR_BASE_URL = os.getenv("EXECUTOR_BASE_URL", "http://executor-ts:3000")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("EXECUTOR_TIMEOUT_SECONDS", "20"))


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


@lru_cache(maxsize=1)
def get_executor_client() -> ExecutorClient:
    return ExecutorClient(
        base_url=EXECUTOR_BASE_URL,
        timeout_seconds=REQUEST_TIMEOUT_SECONDS,
    )


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
    return [sign_transfer, execute_swap]


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
    return {
        "service": "OntologyAgent-brain-py",
        "status": "ok",
        "x402Network": os.getenv("X402_NETWORK", BASE_SEPOLIA_NETWORK),
        "x402PayToConfigured": bool(os.getenv("X402_PAY_TO")),
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
