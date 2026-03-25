import os
from functools import lru_cache
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from executor_client import ExecutorClient, ExecutorClientError
from paid_request_flow import PaidRequestFlowError, request_with_payment_retry

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


class PaymentIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    to: str = Field(description="x402 收款地址")
    amountEth: str = Field(
        description="支付金额（ETH 字符串）",
        pattern=r"^\d+(\.\d+)?$",
    )
    maxRetries: int | None = Field(
        default=None,
        ge=0,
        le=3,
        description="x402 重试次数（0-3）",
    )


class SwapTxIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    to: str = Field(description="swap 目标合约地址")
    valueEth: str = Field(
        default="0",
        description="附带 ETH 数量（字符串）",
        pattern=r"^\d+(\.\d+)?$",
    )
    data: str | None = Field(default=None, description="可选 calldata（0x...）")


class UserOperationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(description="UserOperation 目标地址")
    maxCostEth: str = Field(
        description="允许最大成本（ETH 字符串）",
        pattern=r"^\d+(\.\d+)?$",
    )
    raw: dict[str, Any] = Field(description="完整 UserOperation 原始字段")


class ExecutePaymentFlowIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apiUrl: HttpUrl = Field(description="上游 API URL")
    apiMethod: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = Field(
        default="POST",
        description="请求方法",
    )
    apiHeaders: dict[str, str] | None = Field(default=None, description="可选请求头")
    apiBody: Any | None = Field(default=None, description="可选请求体")
    payment: PaymentIntent = Field(description="x402 支付参数")
    swapTx: SwapTxIntent | None = Field(default=None, description="可选 swap 交易参数")
    userOperation: UserOperationIntent | None = Field(
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


def sign_transfer_tool(to: str, amountEth: str) -> dict[str, Any]:
    intent = SignTransferIntent(to=to, amountEth=amountEth)
    return get_executor_client().sign_transfer(
        to=intent.to,
        amount_eth=intent.amountEth,
    )


def execute_swap_tool(
    apiUrl: str,
    payment: dict[str, Any],
    apiMethod: str = "POST",
    apiHeaders: dict[str, str] | None = None,
    apiBody: Any | None = None,
    swapTx: dict[str, Any] | None = None,
    userOperation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    intent = ExecutePaymentFlowIntent(
        apiUrl=apiUrl,
        apiMethod=apiMethod,
        apiHeaders=apiHeaders,
        apiBody=apiBody,
        payment=payment,
        swapTx=swapTx,
        userOperation=userOperation,
    )
    client = get_executor_client()
    payments: list[dict[str, Any]] = []
    max_retries = 1 if intent.payment.maxRetries is None else intent.payment.maxRetries

    def send_payment(attempt: int) -> str:
        payment_execution = client.submit_execution(
            to=intent.payment.to,
            value_eth=intent.payment.amountEth,
        )
        payments.append(
            {
                "attempt": attempt,
                "transaction": payment_execution["execution"],
                "settlement": payment_execution["settlement"],
            }
        )
        return str(payment_execution["execution"]["txHash"])

    payment_result = request_with_payment_retry(
        url=str(intent.apiUrl),
        method=intent.apiMethod,
        headers=intent.apiHeaders,
        body=intent.apiBody,
        max_retries=max_retries,
        timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        send_payment=send_payment,
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
        "payment": {
            "upstream": payment_result["upstream"],
            "payments": payments,
        },
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
            "执行带 x402 自动支付重试的请求，并可选提交链上交易或 ERC-4337 "
            "UserOperation。"
        ),
        func=execute_swap_tool,
        args_schema=ExecutePaymentFlowIntent,
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
def health() -> dict[str, str]:
    return {"service": "OntologyAgent-brain-py", "status": "ok"}


@app.post("/mock-x402")
def mock_x402(request: Request):
    payment_tx_hash = request.headers.get("x-payment-tx-hash")
    if not payment_tx_hash:
        return JSONResponse(
            status_code=402,
            content={
                "error": "payment_required",
                "message": "send on-chain payment then retry",
            },
        )

    return {
        "ok": True,
        "accepted_payment_tx_hash": payment_tx_hash,
        "quote": {"tokenIn": "ETH", "tokenOut": "USDC", "price": "demo"},
    }


@app.post("/paid-requests/execute")
def execute_paid_request(request: ExecutePaymentFlowIntent) -> dict[str, Any]:
    try:
        return {
            "ok": True,
            "result": execute_swap_tool(
                apiUrl=str(request.apiUrl),
                apiMethod=request.apiMethod,
                apiHeaders=request.apiHeaders,
                apiBody=request.apiBody,
                payment=request.payment.model_dump(mode="json", exclude_none=True),
                swapTx=request.swapTx.model_dump(mode="json")
                if request.swapTx is not None
                else None,
                userOperation=request.userOperation.model_dump(mode="json")
                if request.userOperation is not None
                else None,
            ),
        }
    except (ExecutorClientError, PaidRequestFlowError, httpx.HTTPError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


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
