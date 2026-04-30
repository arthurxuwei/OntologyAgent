from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Optional

from mcp_tool_metadata import McpToolMetadata, metadata_from_mcp_tool, metadata_from_names


class ChainMcpClientError(Exception):
    pass


ToolCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
ToolLister = Callable[[], Awaitable[list[str]]]


class ChainMcpClient:
    def __init__(
        self,
        server_url: str,
        *,
        tool_caller: Optional[ToolCaller] = None,
        tool_lister: Optional[ToolLister] = None,
    ) -> None:
        self.server_url = server_url if server_url.endswith("/") else f"{server_url}/"
        self._tool_caller = tool_caller
        self._tool_lister = tool_lister

    async def list_tools(self) -> list[str]:
        return [tool.name for tool in await self.describe_tools()]

    async def describe_tools(self) -> list[McpToolMetadata]:
        if self._tool_lister is not None:
            return metadata_from_names(await self._tool_lister())

        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(self.server_url) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = await session.list_tools()
                return [metadata_from_mcp_tool(tool) for tool in response.tools]

    async def call_tool(
        self,
        tool_name: str,
        arguments: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if self._tool_caller is not None:
            return await self._tool_caller(tool_name, arguments or {})

        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(self.server_url) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = await session.call_tool(tool_name, arguments or {})
                return normalize_tool_response(response)


def normalize_tool_response(response: Any) -> dict[str, Any]:
    structured_content = getattr(response, "structuredContent", None)
    payload: dict[str, Any]
    if isinstance(structured_content, dict):
        payload = dict(structured_content)
    else:
        content = getattr(response, "content", None)
        if not isinstance(content, list):
            payload = {"raw": content}
        else:
            text_fragments: list[str] = []
            for item in content:
                text = getattr(item, "text", None)
                if isinstance(text, str) and text:
                    text_fragments.append(text)

            if not text_fragments:
                payload = {"raw": content}
            else:
                combined = "\n".join(text_fragments).strip()
                try:
                    parsed = json.loads(combined)
                except json.JSONDecodeError:
                    payload = {"text": combined}
                else:
                    payload = parsed if isinstance(parsed, dict) else {"value": parsed}

    is_error = getattr(response, "isError", False)
    if isinstance(is_error, bool):
        payload["isError"] = is_error

    return payload
