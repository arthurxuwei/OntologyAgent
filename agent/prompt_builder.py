from __future__ import annotations

from skill_loader import SkillCatalog


BASE_AGENT_PROMPT = (
    "You are OntologyAgent. You are a pure orchestration agent. "
    "Use only tools exposed by the active skills. Do not invent tool results."
)


def build_agent_prompt(catalog: SkillCatalog) -> str:
    return BASE_AGENT_PROMPT + catalog.instructions_text()
