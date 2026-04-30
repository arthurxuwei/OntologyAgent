from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from mcp_tool_metadata import McpToolMetadata


ToolInvoker = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def python_type_from_json_schema(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return Any
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), None)
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list[Any],
        "object": dict[str, Any],
    }.get(schema_type, Any)


def args_model_from_json_schema(
    tool_name: str,
    schema: dict[str, Any],
) -> type[BaseModel]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required")
    required_names = set(required if isinstance(required, list) else [])

    fields: dict[str, tuple[Any, Any]] = {}
    for field_name, field_schema in properties.items():
        if not isinstance(field_name, str):
            continue
        schema_dict = field_schema if isinstance(field_schema, dict) else {}
        default = ... if field_name in required_names else schema_dict.get("default", None)
        fields[field_name] = (
            python_type_from_json_schema(schema_dict),
            Field(default, description=schema_dict.get("description")),
        )

    model_name = "".join(part.capitalize() for part in tool_name.split("_")) + "Args"
    return create_model(model_name, __config__=ConfigDict(extra="forbid"), **fields)


def make_mcp_structured_tool(
    metadata: McpToolMetadata,
    invoker: ToolInvoker,
) -> StructuredTool:
    async def invoke(**kwargs: Any) -> dict[str, Any]:
        return await invoker(metadata.name, kwargs)

    return StructuredTool.from_function(
        name=metadata.name,
        description=metadata.description,
        args_schema=args_model_from_json_schema(metadata.name, metadata.input_schema),
        coroutine=invoke,
    )
