from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mcp_runtime import McpRuntime
from prompt_builder import build_agent_prompt
from skill_loader import SkillCatalog, load_skill_catalog


app = FastAPI(title="OntologyAgent agent")
logger = logging.getLogger(__name__)

CHAT_PAGE_PATH = Path(__file__).resolve().parent / "web" / "chat.html"
DASHBOARD_PAGE_PATH = Path(__file__).resolve().parent / "web" / "dashboard.html"
SKILLS_DIR = Path(__file__).resolve().parent / "skills"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
EMPTY_FINAL_OUTPUT_FALLBACK = "Model returned an empty response. Please retry or change model configuration."

_discovered_tools: Optional[list[StructuredTool]] = None


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    input: str = Field(min_length=1, description="User natural language instruction")


class AgentSessionCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    sessionId: str


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    input: str = Field(min_length=1, description="Current user message")


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
def get_skill_catalog() -> SkillCatalog:
    return load_skill_catalog(SKILLS_DIR)


@lru_cache(maxsize=1)
def get_mcp_runtime() -> McpRuntime:
    return McpRuntime(get_skill_catalog())


def clear_discovered_tool_cache() -> None:
    global _discovered_tools
    _discovered_tools = None
    get_mcp_runtime.cache_clear()


async def refresh_discovered_tool_cache() -> None:
    global _discovered_tools
    _discovered_tools = await get_mcp_runtime().discover_tools(os.environ)


def _in_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def build_tools() -> list[StructuredTool]:
    if _discovered_tools is not None:
        return list(_discovered_tools)
    if _in_running_loop():
        return []
    try:
        return asyncio.run(get_mcp_runtime().discover_tools(os.environ))
    except Exception as error:
        logger.debug("Failed to discover MCP runtime tools: %s", error)
        return []


def get_agent_prompt() -> str:
    return build_agent_prompt(get_skill_catalog())


def get_openai_base_url() -> Optional[str]:
    value = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_ENDPOINT")
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _provider_supports_streamed_tool_execution() -> bool:
    base_url = get_openai_base_url()
    if not base_url:
        return True
    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    if hostname == "packyapi.com" or hostname.endswith(".packyapi.com"):
        return False
    return True


def get_ledger_http_url() -> str:
    return os.getenv("LEDGER_HTTP_URL", "http://localhost:8092").rstrip("/")


def get_agent_wallet_state_path() -> Path:
    configured = os.getenv("AGENT_WALLET_STATE_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "chain" / "data" / "agent_wallet_state.json"


def _normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def load_agent_wallet_bindings() -> list[dict[str, Any]]:
    path = get_agent_wallet_state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    bindings = payload.get("agentWalletBindings") if isinstance(payload, dict) else None
    if not isinstance(bindings, list):
        return []
    return [binding for binding in bindings if isinstance(binding, dict)]


async def fetch_ledger_state() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{get_ledger_http_url()}/ledger/state")
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Ledger service is unavailable: {error}",
        ) from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Ledger service returned invalid state")
    return payload


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
    llm = ChatOpenAI(api_key=api_key, **llm_kwargs)
    return create_react_agent(model=llm, tools=build_tools(), prompt=get_agent_prompt())


def _normalize_message_content(content: Any) -> str:
    if content is None:
        return ""
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
        return "".join(parts)
    return str(content)


def _is_empty_message_content(content: Any) -> bool:
    return not _normalize_message_content(content).strip()


def _extract_final_output(messages: list[Any]) -> str:
    final_message = messages[-1] if messages else None
    final_content = getattr(final_message, "content", None)
    if getattr(final_message, "type", None) == "ai" and not _is_empty_message_content(
        final_content
    ):
        return _normalize_message_content(final_content)
    if not _is_empty_message_content(final_content):
        return EMPTY_FINAL_OUTPUT_FALLBACK
    logger.warning("Agent returned empty final output; message_count=%d", len(messages))
    return EMPTY_FINAL_OUTPUT_FALLBACK


def _align_final_message_output(messages: list[Any], output: str) -> list[Any]:
    if output != EMPTY_FINAL_OUTPUT_FALLBACK:
        return messages
    aligned_messages = list(messages)
    if aligned_messages and getattr(aligned_messages[-1], "type", None) == "ai":
        message = aligned_messages[-1]
        if hasattr(message, "model_copy"):
            aligned_messages[-1] = message.model_copy(update={"content": output})
        else:
            aligned_messages[-1] = AIMessage(content=output)
        return aligned_messages
    aligned_messages.append(AIMessage(content=output))
    return aligned_messages


def _is_valid_tool_message(message: Any) -> bool:
    if getattr(message, "type", None) != "tool":
        return True
    tool_call_id = getattr(message, "tool_call_id", None)
    return isinstance(tool_call_id, str) and bool(tool_call_id.strip())


def _sanitize_session_messages(messages: list[Any]) -> list[Any]:
    sanitized = [message for message in messages if _is_valid_tool_message(message)]
    dropped = len(messages) - len(sanitized)
    if dropped:
        logger.warning("Dropped %d invalid tool messages from session history", dropped)
    return sanitized


def _is_tool_message_validation_error(error: Exception) -> bool:
    if not isinstance(error, ValidationError):
        return False
    message = str(error)
    return "ToolMessage" in message and "tool_call_id" in message


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _consume_stream_item(
    item: tuple[str, Any], latest_messages: Optional[list[Any]]
) -> tuple[Optional[str], Optional[list[Any]]]:
    mode, payload = item
    if mode == "messages":
        chunk, _metadata = payload
        text = _normalize_message_content(getattr(chunk, "content", None))
        return (text or None), latest_messages
    if mode == "values" and isinstance(payload, dict):
        messages = payload.get("messages")
        if isinstance(messages, list):
            return (None, list(messages))
    return (None, latest_messages)


def _atomic_to_usdc(value: Any) -> float:
    try:
        return float(Decimal(str(value or "0")) / Decimal("1000000"))
    except (InvalidOperation, ValueError):
        return 0.0


def _atomic_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _short_address(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "ledger-account"
    if len(text) <= 18:
        return text
    return f"{text[:10]}…{text[-6:]}"


def _bindings_by_agent_id(bindings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_agent_id: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        agent_id = str(binding.get("agentId") or "").strip()
        if agent_id:
            by_agent_id[agent_id] = binding
    return by_agent_id


def _dashboard_counterparty(
    entry: dict[str, Any], escrow_by_id: dict[str, dict[str, Any]]
) -> str:
    escrow_id = entry.get("escrowId")
    if isinstance(escrow_id, str) and escrow_id in escrow_by_id:
        escrow = escrow_by_id[escrow_id]
        agent_id = entry.get("agentId")
        buyer_id = escrow.get("buyerAgentId")
        seller_id = escrow.get("sellerAgentId")
        if agent_id == buyer_id and seller_id:
            return str(seller_id)
        if agent_id == seller_id and buyer_id:
            return str(buyer_id)
        return str(escrow.get("description") or escrow_id)
    entry_type = str(entry.get("entryType") or "")
    reason = str(entry.get("reason") or "").strip()
    if "onramp" in entry_type or "onramp" in reason.lower():
        return "Coinbase Onramp"
    return reason or "Ledger"


def _dashboard_transaction(
    entry: dict[str, Any], escrow_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    entry_type = str(entry.get("entryType") or "ledger")
    available_delta = _atomic_decimal(entry.get("availableDeltaAtomic"))
    locked_delta = _atomic_decimal(entry.get("lockedDeltaAtomic"))
    escrow = escrow_by_id.get(str(entry.get("escrowId") or ""))
    amount_atomic = (
        _atomic_decimal(escrow.get("amountAtomic"))
        if escrow
        else max(abs(available_delta), abs(locked_delta))
    )
    agent_id = entry.get("agentId")
    direction = "out" if available_delta < 0 or locked_delta > 0 else "in"
    if entry_type == "escrow_release" and escrow and agent_id == escrow.get("buyerAgentId"):
        direction = "out"
    status = "released"
    if entry_type == "escrow_lock":
        status = "locked"
    elif entry_type == "escrow_refund":
        status = "refunded"
    elif "onramp" in entry_type or entry_type == "credit":
        status = "onramp"
    role = "payer" if direction == "out" else "payee"
    if status == "onramp":
        role = "deposit"
    elif status == "refunded":
        role = "refund"
    return {
        "id": entry.get("entryId") or entry.get("escrowId") or "ledger_entry",
        "counterparty": _dashboard_counterparty(entry, escrow_by_id),
        "amount": _atomic_to_usdc(amount_atomic),
        "direction": direction,
        "role": role,
        "status": status,
        "timestamp": entry.get("createdAt") or "ledger",
    }


def build_dashboard_data(
    ledger_state: dict[str, Any],
    wallet_bindings: Optional[list[dict[str, Any]]] = None,
    owner_email: Optional[str] = None,
) -> dict[str, Any]:
    accounts = [
        account
        for account in ledger_state.get("accounts", [])
        if isinstance(account, dict)
    ]
    entries = [
        entry
        for entry in ledger_state.get("entries", [])
        if isinstance(entry, dict)
    ]
    escrows = [
        escrow
        for escrow in ledger_state.get("escrows", [])
        if isinstance(escrow, dict)
    ]
    escrow_by_id = {
        str(escrow.get("escrowId")): escrow
        for escrow in escrows
        if escrow.get("escrowId")
    }
    binding_by_agent_id = _bindings_by_agent_id(wallet_bindings or [])
    normalized_owner_email = _normalize_email(owner_email)
    entries_by_agent: dict[str, list[dict[str, Any]]] = {}
    for entry in sorted(entries, key=lambda item: str(item.get("createdAt") or ""), reverse=True):
        agent_id = str(entry.get("agentId") or "").strip()
        if not agent_id:
            continue
        entries_by_agent.setdefault(agent_id, []).append(entry)

    agents: dict[str, Any] = {}
    for account in accounts:
        agent_id = str(account.get("agentId") or "").strip()
        if not agent_id:
            continue
        agent_entries = entries_by_agent.get(agent_id, [])
        binding = binding_by_agent_id.get(agent_id, {})
        if normalized_owner_email and _normalize_email(binding.get("email")) != normalized_owner_email:
            continue
        lifetime_in = sum(
            _atomic_to_usdc(entry.get("availableDeltaAtomic"))
            for entry in agent_entries
            if _atomic_decimal(entry.get("availableDeltaAtomic")) > 0
        )
        lifetime_out = sum(
            _atomic_to_usdc(abs(_atomic_decimal(entry.get("availableDeltaAtomic"))))
            for entry in agent_entries
            if _atomic_decimal(entry.get("availableDeltaAtomic")) < 0
        )
        wallet_address = (
            account.get("walletAddress")
            or account.get("circleWalletAddress")
            or account.get("circleWalletId")
            or binding.get("walletAddress")
            or binding.get("circleWalletId")
            or agent_id
        )
        agent_name = str(binding.get("agentName") or agent_id)
        agents[agent_id] = {
            "agent": {
                "id": agent_id,
                "name": agent_name,
                "role": "Agent Wallet Account",
                "walletAddress": _short_address(wallet_address),
                "claimedDaysAgo": 0,
                "ownerEmail": _normalize_email(binding.get("email")),
            },
            "balance": {
                "available": _atomic_to_usdc(account.get("availableAtomic")),
                "locked": _atomic_to_usdc(account.get("lockedAtomic")),
                "lifetimeIn": round(lifetime_in, 6),
                "lifetimeOut": round(lifetime_out, 6),
            },
            "transactions": [
                _dashboard_transaction(entry, escrow_by_id)
                for entry in agent_entries
            ],
            "settings": {"limits": {"perTradeCap": 0.01}},
        }

    return {
        "agents": agents,
        "defaultAgentId": next(iter(agents), None),
        "source": "ledger",
    }


def _empty_dashboard_agent(binding: dict[str, Any]) -> dict[str, Any]:
    agent_id = str(binding.get("agentId") or "").strip()
    wallet_address = binding.get("walletAddress") or binding.get("circleWalletId") or agent_id
    return {
        "agent": {
            "id": agent_id,
            "name": str(binding.get("agentName") or agent_id),
            "role": "Agent Wallet Account",
            "walletAddress": _short_address(wallet_address),
            "claimedDaysAgo": 0,
            "ownerEmail": _normalize_email(binding.get("email")),
        },
        "balance": {
            "available": 0.0,
            "locked": 0.0,
            "lifetimeIn": 0.0,
            "lifetimeOut": 0.0,
        },
        "transactions": [],
        "settings": {"limits": {"perTradeCap": 0.01}},
    }


def build_claimable_agents(
    *,
    email: str,
    ledger_state: dict[str, Any],
    wallet_bindings: list[dict[str, Any]],
    claimed_agent_ids: list[str],
) -> dict[str, Any]:
    normalized_email = _normalize_email(email)
    if not normalized_email:
        raise HTTPException(status_code=400, detail="email is required")
    claimed = {str(agent_id).strip() for agent_id in claimed_agent_ids if str(agent_id).strip()}
    dashboard_state = build_dashboard_data(ledger_state, wallet_bindings)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    matching_bindings = sorted(
        wallet_bindings,
        key=lambda binding: str(binding.get("updatedAt") or ""),
        reverse=True,
    )
    for binding in matching_bindings:
        agent_id = str(binding.get("agentId") or "").strip()
        if not agent_id or agent_id in seen or agent_id in claimed:
            continue
        if _normalize_email(binding.get("email")) != normalized_email:
            continue
        seen.add(agent_id)
        wallet_address = binding.get("walletAddress") or binding.get("circleWalletId") or agent_id
        dashboard_agent = dashboard_state["agents"].get(agent_id) or _empty_dashboard_agent(binding)
        candidates.append(
            {
                "agentId": agent_id,
                "agentName": str(binding.get("agentName") or agent_id),
                "ownerEmail": normalized_email,
                "walletAddress": str(wallet_address),
                "displayWalletAddress": _short_address(wallet_address),
                "circleWalletId": binding.get("circleWalletId"),
                "claimStatus": "unclaimed",
                "dashboard": dashboard_agent,
            }
        )
    return {
        "email": normalized_email,
        "agents": candidates,
        "source": "agent-wallet-bindings",
    }


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
    await refresh_discovered_tool_cache()


@app.get("/", response_class=HTMLResponse)
def dashboard_home() -> FileResponse:
    return FileResponse(DASHBOARD_PAGE_PATH, headers=NO_CACHE_HEADERS)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> FileResponse:
    return FileResponse(DASHBOARD_PAGE_PATH, headers=NO_CACHE_HEADERS)


@app.get("/chat", response_class=HTMLResponse)
def chat_page() -> FileResponse:
    return FileResponse(CHAT_PAGE_PATH, headers=NO_CACHE_HEADERS)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{get_ledger_http_url()}/")
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Ledger admin page is unavailable: {error}",
        ) from error
    return HTMLResponse(response.text, headers=NO_CACHE_HEADERS)


@app.get("/dashboard/ledger-state")
async def dashboard_ledger_state() -> dict[str, Any]:
    return await fetch_ledger_state()


@app.get("/dashboard/data")
async def dashboard_data(email: str = "") -> dict[str, Any]:
    return build_dashboard_data(
        await fetch_ledger_state(),
        load_agent_wallet_bindings(),
        owner_email=email,
    )


@app.get("/dashboard/claimable-agents")
async def dashboard_claimable_agents(email: str, claimed: str = "") -> dict[str, Any]:
    claimed_agent_ids = [
        item.strip()
        for item in claimed.split(",")
        if item.strip()
    ]
    return build_claimable_agents(
        email=email,
        ledger_state=await fetch_ledger_state(),
        wallet_bindings=load_agent_wallet_bindings(),
        claimed_agent_ids=claimed_agent_ids,
    )


def _proxy_headers(request: Request) -> dict[str, str]:
    excluded = {"host", "content-length", "connection"}
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in excluded
    }


@app.api_route("/ledger/{path:path}", methods=["GET", "POST"])
@app.api_route("/onramp/{path:path}", methods=["GET", "POST"])
async def ledger_api_proxy(path: str, request: Request) -> Response:
    upstream_url = f"{get_ledger_http_url()}{request.url.path}"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            upstream_response = await client.request(
                request.method,
                upstream_url,
                content=await request.body(),
                headers=_proxy_headers(request),
            )
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Ledger service is unavailable: {error}",
        ) from error
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type"),
    )


@app.get("/health")
def health() -> dict[str, Any]:
    runtime = get_mcp_runtime()
    tools = build_tools()
    return {
        "service": "OntologyAgent-agent",
        "status": "ok",
        "modelName": os.getenv("BRAIN_AGENT_MODEL", "gpt-4o-mini"),
        "openaiBaseUrl": get_openai_base_url(),
        "skills": [skill.name for skill in get_skill_catalog().skills],
        "mcpServers": sorted(get_skill_catalog().server_names()),
        "mcpHealth": runtime.health(),
        "toolCount": len(tools),
        "tools": [tool.name for tool in tools],
    }


@app.post("/agent/reload-runtime")
async def reload_agent_runtime() -> dict[str, Any]:
    get_skill_catalog.cache_clear()
    get_mcp_runtime.cache_clear()
    clear_discovered_tool_cache()
    await refresh_discovered_tool_cache()
    get_agent_graph.cache_clear()
    skill_catalog = get_skill_catalog()
    return {
        "ok": True,
        "skills": [skill.name for skill in skill_catalog.skills],
        "mcpServers": sorted(skill_catalog.server_names()),
    }


@app.post("/agent/sessions")
def create_agent_session() -> AgentSessionCreateResponse:
    session = get_session_store().create()
    return AgentSessionCreateResponse(sessionId=session.session_id)


@app.get("/agent/sessions/{session_id}")
def get_agent_session(session_id: str) -> AgentSessionStateResponse:
    try:
        session = get_session_store().get(session_id)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail=f"Unknown agent session: {session_id}"
        ) from error
    return AgentSessionStateResponse(
        sessionId=session.session_id,
        messageCount=len(session.messages),
    )


@app.post("/agent/sessions/{session_id}/messages")
async def create_agent_session_message(
    session_id: str, request: AgentChatRequest
) -> AgentChatResponse:
    try:
        session = get_session_store().get(session_id)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail=f"Unknown agent session: {session_id}"
        ) from error
    input_message = HumanMessage(content=request.input)
    pending_messages = [*_sanitize_session_messages(list(session.messages)), input_message]
    result = await _invoke_agent(pending_messages)
    messages = result.get("messages", [])
    output = _extract_final_output(messages)
    session.messages = _sanitize_session_messages(
        _align_final_message_output(list(messages), output)
    )
    return AgentChatResponse(
        sessionId=session.session_id,
        input=request.input,
        output=output,
        messageCount=len(session.messages),
    )


@app.post("/agent/sessions/{session_id}/messages/stream")
async def stream_agent_session_message(
    session_id: str, request: AgentChatRequest
) -> StreamingResponse:
    try:
        session = get_session_store().get(session_id)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail=f"Unknown agent session: {session_id}"
        ) from error

    input_message = HumanMessage(content=request.input)
    existing_messages = _sanitize_session_messages(list(session.messages))
    pending_messages = [*existing_messages, input_message]

    async def event_stream() -> AsyncIterator[str]:
        yield _sse_event("start", {"sessionId": session.session_id, "input": request.input})
        deltas: list[str] = []
        latest_messages: Optional[list[Any]] = None
        try:
            graph = get_agent_graph()
        except Exception as error:
            yield _sse_event("error", {"sessionId": session.session_id, "error": str(error)})
            return

        if not _provider_supports_streamed_tool_execution():
            try:
                result = await graph.ainvoke({"messages": pending_messages})
                latest_messages = result.get("messages")
            except Exception as error:
                yield _sse_event("error", {"sessionId": session.session_id, "error": str(error)})
                return
        else:
            try:
                stream = graph.astream(
                    {"messages": pending_messages},
                    stream_mode=["messages", "values"],
                )
                async for item in stream:
                    delta, latest_messages = _consume_stream_item(item, latest_messages)
                    if delta is not None:
                        deltas.append(delta)
                        yield _sse_event("delta", {"delta": delta})
            except Exception as error:
                if not _is_tool_message_validation_error(error):
                    yield _sse_event(
                        "error",
                        {"sessionId": session.session_id, "error": str(error)},
                    )
                    return
                try:
                    result = await graph.ainvoke({"messages": pending_messages})
                    latest_messages = result.get("messages")
                except Exception as invoke_error:
                    yield _sse_event(
                        "error",
                        {"sessionId": session.session_id, "error": str(invoke_error)},
                    )
                    return

        final_messages = latest_messages or [
            *pending_messages,
            AIMessage(content="".join(deltas)),
        ]
        output = _extract_final_output(final_messages)
        session.messages = _sanitize_session_messages(
            _align_final_message_output(list(final_messages), output)
        )
        yield _sse_event(
            "final",
            {
                "sessionId": session.session_id,
                "output": output,
                "messageCount": len(session.messages),
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/agent/run")
async def run_agent(request: AgentRunRequest) -> dict[str, Any]:
    result = await _invoke_agent([HumanMessage(content=request.input)])
    messages = result.get("messages", [])
    return {
        "ok": True,
        "input": request.input,
        "output": _extract_final_output(messages),
    }
