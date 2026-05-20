from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from prompt_builder import build_agent_prompt
from rest_tool_registry import build_rest_tools
from skill_loader import SkillCatalog, load_skill_catalog


app = FastAPI(title="OntologyAgent agent")
logger = logging.getLogger(__name__)

CHAT_PAGE_PATH = Path(__file__).resolve().parent / "web" / "chat.html"
SKILLS_DIR = Path(__file__).resolve().parent / "skills"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
EMPTY_FINAL_OUTPUT_FALLBACK = "Model returned an empty response. Please retry or change model configuration."

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


def clear_tool_cache() -> None:
    get_agent_graph.cache_clear()


def build_tools() -> list[StructuredTool]:
    return build_rest_tools(os.environ)


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


async def _invoke_agent(messages: list[Any]) -> dict[str, Any]:
    try:
        graph = get_agent_graph()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    try:
        return await graph.ainvoke({"messages": messages})
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/", response_class=HTMLResponse)
@app.get("/chat", response_class=HTMLResponse)
def chat_page() -> FileResponse:
    return FileResponse(CHAT_PAGE_PATH, headers=NO_CACHE_HEADERS)


@app.get("/health")
def health() -> dict[str, Any]:
    tools = build_tools()
    return {
        "service": "OntologyAgent-agent",
        "status": "ok",
        "modelName": os.getenv("BRAIN_AGENT_MODEL", "gpt-4o-mini"),
        "openaiBaseUrl": get_openai_base_url(),
        "skills": [skill.name for skill in get_skill_catalog().skills],
        "toolCount": len(tools),
        "tools": [tool.name for tool in tools],
        "toolTransport": "rest",
        "actionServices": {
            "ledger": os.getenv("LEDGER_HTTP_URL", "http://ledger:8092"),
            "chain": os.getenv("CHAIN_HTTP_URL", "http://chain:8091"),
        },
    }


@app.post("/agent/reload-runtime")
async def reload_agent_runtime() -> dict[str, Any]:
    get_skill_catalog.cache_clear()
    clear_tool_cache()
    get_agent_graph.cache_clear()
    skill_catalog = get_skill_catalog()
    return {
        "ok": True,
        "skills": [skill.name for skill in skill_catalog.skills],
        "tools": [tool.name for tool in build_tools()],
        "toolTransport": "rest",
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
