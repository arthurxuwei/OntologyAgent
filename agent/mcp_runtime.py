from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from langchain_core.tools import StructuredTool

from chain_mcp_client import ChainMcpClient
from skill_loader import SkillCatalog
from tool_schema import make_mcp_structured_tool


ClientFactory = Callable[[str, str], Any]


def default_client_factory(_server: str, url: str) -> Any:
    return ChainMcpClient(url)


class McpRuntime:
    def __init__(
        self,
        catalog: SkillCatalog,
        *,
        client_factory: ClientFactory = default_client_factory,
    ) -> None:
        self._catalog = catalog
        self._client_factory = client_factory
        self._clients: dict[str, Any] = {}
        self._tool_servers: dict[str, str] = {}
        self._health: dict[str, dict[str, Any]] = {}

    async def discover_tools(self, environ: Mapping[str, str]) -> list[StructuredTool]:
        tools: list[StructuredTool] = []
        self._tool_servers = {}
        for server in sorted(self._catalog.server_names()):
            url = self._catalog.server_url(server)
            if not url:
                self._health[server] = {
                    "status": "degraded",
                    "error": f"{server} MCP URL is not configured",
                }
                continue

            client = self._client_factory(server, url)
            self._clients[server] = client
            try:
                metadata = await client.describe_tools()
            except Exception as error:
                self._health[server] = {"status": "degraded", "error": str(error)}
                continue

            server_tool_count = 0
            allowed = self._catalog.exposed_mcp_tools(server)
            hidden = self._catalog.hidden_mcp_tools_for(server)
            for item in metadata:
                if item.name in hidden:
                    continue
                if allowed and item.name not in allowed:
                    continue
                self._tool_servers[item.name] = server
                tools.append(make_mcp_structured_tool(item, self.call_tool))
                server_tool_count += 1
            self._health[server] = {"status": "ok", "toolCount": server_tool_count}
        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        server = self._tool_servers.get(tool_name)
        if server is None:
            return {"tool": tool_name, "isError": True, "error": "tool is not exposed"}

        result = await self._clients[server].call_tool(tool_name, arguments)
        if isinstance(result, dict) and result.get("isError"):
            return {"tool": tool_name, "isError": True, "error": result}
        return {"tool": tool_name, "result": result}

    def health(self) -> dict[str, dict[str, Any]]:
        return dict(self._health)
