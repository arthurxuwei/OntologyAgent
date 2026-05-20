from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    instructions: str = ""


@dataclass(frozen=True)
class SkillCatalog:
    skills: tuple[SkillDefinition, ...]

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

    return SkillDefinition(
        name=name,
        description=_optional_text(manifest.get("description")),
        instructions=instructions,
    )


def _required_text(manifest: dict[str, Any], key: str, path: Path) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Skill manifest {path} must include a non-empty {key}")
    return value.strip()


def _optional_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
