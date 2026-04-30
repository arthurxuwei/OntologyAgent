from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class McpServerDefinition:
    name: str
    url: str | None = None
    tools: tuple[str, ...] = ()
    hidden_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    mcp_tools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    hidden_mcp_tools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    mcp_servers: dict[str, McpServerDefinition] = field(default_factory=dict)
    instructions: str = ""


@dataclass(frozen=True)
class SkillCatalog:
    skills: tuple[SkillDefinition, ...]

    def server_names(self) -> set[str]:
        return {
            server_name
            for skill in self.skills
            for server_name in skill.mcp_servers
        }

    def server_url(self, server: str) -> str | None:
        for skill in self.skills:
            definition = skill.mcp_servers.get(server)
            if definition is not None:
                return definition.url
        return None

    def exposed_mcp_tools(self, server: str) -> set[str]:
        return {
            tool
            for skill in self.skills
            for tool in skill.mcp_tools.get(server, ())
        }

    def hidden_mcp_tools_for(self, server: str) -> set[str]:
        return {
            tool
            for skill in self.skills
            for tool in skill.hidden_mcp_tools.get(server, ())
        }

    def instructions_text(self) -> str:
        sections = [
            f"Skill: {skill.name}\n{skill.instructions.strip()}"
            for skill in self.skills
            if skill.instructions.strip()
        ]
        if not sections:
            return ""
        return "\n\nAvailable skills:\n" + "\n\n".join(sections)


def load_skill_catalog(skills_dir: Path) -> SkillCatalog:
    if not skills_dir.exists():
        return SkillCatalog(skills=())

    skills: list[SkillDefinition] = []
    for manifest_path in sorted(skills_dir.glob("*/skill.json")):
        manifest = _load_json(manifest_path)
        if manifest.get("enabled", True) is False:
            continue
        skills.append(_skill_from_manifest(manifest_path, manifest))
    return SkillCatalog(skills=tuple(skills))


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Skill manifest must be an object: {path}")
    return payload


def _skill_from_manifest(path: Path, manifest: dict[str, Any]) -> SkillDefinition:
    name = _required_text(manifest, "name", path)
    instructions = ""
    instructions_path = manifest.get("instructions")
    if isinstance(instructions_path, str) and instructions_path.strip():
        instructions = (path.parent / instructions_path).read_text(encoding="utf-8")
    mcp_servers = _mcp_server_definitions(
        manifest.get("mcpServers"),
        manifest.get("hiddenTools"),
        path,
    )

    return SkillDefinition(
        name=name,
        description=_optional_text(manifest.get("description")),
        mcp_tools={server: definition.tools for server, definition in mcp_servers.items()},
        hidden_mcp_tools={
            server: definition.hidden_tools
            for server, definition in mcp_servers.items()
        },
        mcp_servers=mcp_servers,
        instructions=instructions,
    )


def _required_text(manifest: dict[str, Any], key: str, path: Path) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Skill manifest {path} must include a non-empty {key}")
    return value.strip()


def _optional_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _mcp_server_definitions(
    value: Any,
    hidden_tools: Any,
    path: Path,
) -> dict[str, McpServerDefinition]:
    if not isinstance(value, dict):
        return {}

    hidden_by_server = hidden_tools if isinstance(hidden_tools, dict) else {}
    result: dict[str, McpServerDefinition] = {}
    for server, definition in value.items():
        if not isinstance(server, str) or not server.strip():
            continue
        if not isinstance(definition, dict):
            raise ValueError(f"Skill manifest {path} has invalid mcpServers.{server}")

        url = definition.get("url")
        has_url = isinstance(url, str) and bool(url.strip())
        if not has_url:
            raise ValueError(
                f"Skill manifest {path} must include mcpServers.{server}.url"
            )

        server_name = server.strip()
        result[server_name] = McpServerDefinition(
            name=server_name,
            url=url.strip(),
            tools=tuple(_string_list(definition.get("tools"))),
            hidden_tools=tuple(_string_list(hidden_by_server.get(server_name))),
        )
    return result
