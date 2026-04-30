from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class McpToolMetadata:
    name: str
    description: str
    input_schema: dict[str, Any]


def metadata_from_mcp_tool(tool: Any) -> McpToolMetadata:
    input_schema = (
        getattr(tool, "inputSchema", None)
        or getattr(tool, "input_schema", None)
        or {}
    )
    if not isinstance(input_schema, dict):
        input_schema = {}

    description = getattr(tool, "description", None)
    if not isinstance(description, str) or not description.strip():
        description = f"Call MCP tool {tool.name}."

    return McpToolMetadata(
        name=str(tool.name),
        description=description,
        input_schema=input_schema,
    )


def metadata_from_names(tool_names: list[str]) -> list[McpToolMetadata]:
    return [
        McpToolMetadata(
            name=name,
            description=f"Call MCP tool {name}.",
            input_schema={"type": "object", "properties": {}},
        )
        for name in tool_names
    ]
