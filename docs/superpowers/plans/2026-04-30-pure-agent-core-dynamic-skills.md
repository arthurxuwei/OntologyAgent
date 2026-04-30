# Pure Agent Core Dynamic Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `agent` a pure chat/session/MCP orchestration core with all financial-domain knowledge and tools loaded dynamically from enabled skills and external MCP servers.

**Architecture:** Agent Core loads skill manifests, builds prompt context from enabled skill instructions, discovers MCP tools from skill-declared servers, and exposes only allowlisted tools. Domain logic moves to MCP services: existing `chain`, existing `freqtrade`, and new `wallet-ledger-payment`.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, LangChain `StructuredTool`, MCP Streamable HTTP, unittest, Docker Compose.

---

## File Structure

- Create `agent/prompt_builder.py`: compose minimal base prompt and enabled skill instructions.
- Create `agent/tool_schema.py`: convert MCP input schemas into Pydantic models and `StructuredTool` instances.
- Create `agent/mcp_runtime.py`: generic MCP server discovery, health, allowlist filtering, and invocation.
- Modify `agent/skill_loader.py`: support skill manifest v2 while keeping current manifest compatibility during migration.
- Modify `agent/main.py`: remove domain registries and use `mcp_runtime` for all exposed tools.
- Modify `agent/web/chat.html`: remove Agent Wallet MVP panel and wallet-specific JavaScript.
- Create `wallet-ledger-payment/`: new MCP service for payment routing, ledger, escrow, and optional Agent Wallet domain state.
- Modify `docker-compose.yml`: add `wallet-ledger-payment` and pass `WALLET_LEDGER_PAYMENT_MCP_URL` to `agent`.
- Modify `agent/skills/*/skill.json`: migrate to v2 `mcpServers` format.
- Modify tests under `agent/tests/`: cover pure core, dynamic skills, MCP runtime, and clean-core import guard.
- Create tests under `wallet-ledger-payment/tests/`: cover payment routing and ledger/escrow MCP tools.

---

## Task 1: Upgrade Skill Loader to Manifest v2

**Files:**
- Modify: `agent/skill_loader.py`
- Test: `agent/tests/test_skill_loader.py`

- [ ] **Step 1: Write failing tests for v2 manifests**

Add tests that prove skills can declare MCP servers, URL env vars, tool allowlists, hidden tools, and instructions.

```python
def test_load_skill_catalog_reads_v2_mcp_servers(self) -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmpdir:
        skill_dir = Path(tmpdir) / "payment-routing"
        skill_dir.mkdir()
        (skill_dir / "instructions.md").write_text(
            "Route payments before settlement.",
            encoding="utf-8",
        )
        (skill_dir / "skill.json").write_text(
            json.dumps(
                {
                    "name": "payment-routing",
                    "enabled": True,
                    "description": "Payment routing skill",
                    "instructions": "instructions.md",
                    "mcpServers": {
                        "wallet-ledger-payment": {
                            "urlEnv": "WALLET_LEDGER_PAYMENT_MCP_URL",
                            "tools": ["route_payment_intent"],
                        }
                    },
                    "hiddenTools": {
                        "wallet-ledger-payment": ["internal_debug_tool"]
                    },
                }
            ),
            encoding="utf-8",
        )

        catalog = load_skill_catalog(Path(tmpdir))

    self.assertEqual(catalog.server_names(), {"wallet-ledger-payment"})
    self.assertEqual(
        catalog.server_url_env("wallet-ledger-payment"),
        "WALLET_LEDGER_PAYMENT_MCP_URL",
    )
    self.assertEqual(
        catalog.exposed_mcp_tools("wallet-ledger-payment"),
        {"route_payment_intent"},
    )
    self.assertEqual(
        catalog.hidden_mcp_tools_for("wallet-ledger-payment"),
        {"internal_debug_tool"},
    )
    self.assertIn("Route payments before settlement.", catalog.instructions_text())
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH='agent'; python -m unittest agent.tests.test_skill_loader
```

Expected: FAIL because `server_names()` and `server_url_env()` do not exist yet.

- [ ] **Step 3: Implement v2-compatible skill models**

Update `agent/skill_loader.py` with these fields and helpers:

```python
@dataclass(frozen=True)
class McpServerDefinition:
    name: str
    url_env: str
    tools: tuple[str, ...] = ()
    hidden_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    local_tools: tuple[str, ...] = ()
    mcp_tools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    hidden_mcp_tools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    mcp_servers: dict[str, McpServerDefinition] = field(default_factory=dict)
    instructions: str = ""
```

Add catalog helpers:

```python
def server_names(self) -> set[str]:
    return {
        server_name
        for skill in self.skills
        for server_name in skill.mcp_servers
    }


def server_url_env(self, server: str) -> str | None:
    for skill in self.skills:
        definition = skill.mcp_servers.get(server)
        if definition is not None:
            return definition.url_env
    return None
```

Update `_skill_from_manifest()` to parse both legacy `mcpTools` and v2 `mcpServers`. For v2, merge tools into `mcp_tools` so existing callers continue to work.

- [ ] **Step 4: Run skill loader tests**

Run:

```powershell
$env:PYTHONPATH='agent'; python -m unittest agent.tests.test_skill_loader
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add agent/skill_loader.py agent/tests/test_skill_loader.py
git commit -m "feat: support skill manifest v2 mcp servers"
```

---

## Task 2: Extract Prompt Builder

**Files:**
- Create: `agent/prompt_builder.py`
- Modify: `agent/main.py`
- Test: `agent/tests/test_prompt_builder.py`

- [ ] **Step 1: Write failing prompt builder tests**

Create `agent/tests/test_prompt_builder.py`:

```python
import unittest

from prompt_builder import build_agent_prompt
from skill_loader import SkillCatalog, SkillDefinition


class PromptBuilderTests(unittest.TestCase):
    def test_build_agent_prompt_uses_minimal_core_and_skill_instructions(self) -> None:
        catalog = SkillCatalog(
            skills=(
                SkillDefinition(
                    name="payment-routing",
                    description="",
                    instructions="Route payments before settlement.",
                ),
            )
        )

        prompt = build_agent_prompt(catalog)

        self.assertIn("You are OntologyAgent.", prompt)
        self.assertIn("Route payments before settlement.", prompt)
        self.assertNotIn("x402 buyer flow", prompt)
        self.assertNotIn("ledger escrow", prompt.lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH='agent'; python -m unittest agent.tests.test_prompt_builder
```

Expected: FAIL because `prompt_builder.py` does not exist.

- [ ] **Step 3: Implement prompt builder**

Create `agent/prompt_builder.py`:

```python
from __future__ import annotations

from skill_loader import SkillCatalog


BASE_AGENT_PROMPT = (
    "You are OntologyAgent. You are a pure orchestration agent. "
    "Use only tools exposed by the active skills. Do not invent tool results."
)


def build_agent_prompt(catalog: SkillCatalog) -> str:
    return BASE_AGENT_PROMPT + catalog.instructions_text()
```

Update `agent/main.py`:

```python
from prompt_builder import build_agent_prompt


def get_agent_prompt() -> str:
    return build_agent_prompt(get_skill_catalog())
```

Remove `SYSTEM_PROMPT` and `get_knowledge_base()` usage from Agent Core after the tests pass. Keep `knowledge_loader.py` only if another task still needs compatibility; otherwise delete it in Task 8.

- [ ] **Step 4: Run prompt and existing skill tests**

Run:

```powershell
$env:PYTHONPATH='agent'; python -m unittest agent.tests.test_prompt_builder agent.tests.test_skill_loader
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add agent/prompt_builder.py agent/main.py agent/tests/test_prompt_builder.py
git commit -m "refactor: build agent prompt from active skills"
```

---

## Task 3: Add Generic MCP Tool Schema Conversion

**Files:**
- Create: `agent/tool_schema.py`
- Modify: `agent/main.py`
- Test: `agent/tests/test_tool_schema.py`

- [ ] **Step 1: Write failing tests**

Create `agent/tests/test_tool_schema.py`:

```python
import asyncio
import unittest

from mcp_tool_metadata import McpToolMetadata
from tool_schema import make_mcp_structured_tool


class ToolSchemaTests(unittest.TestCase):
    def test_make_tool_converts_json_schema_to_structured_tool(self) -> None:
        calls = []

        async def invoker(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
            calls.append((tool_name, arguments))
            return {"ok": True, "arguments": arguments}

        tool = make_mcp_structured_tool(
            McpToolMetadata(
                name="route_payment_intent",
                description="Route payment intent.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "purpose": {"type": "string", "description": "Payment purpose"},
                        "requiresAcceptance": {"type": "boolean", "default": False},
                    },
                    "required": ["purpose"],
                },
            ),
            invoker,
        )

        result = asyncio.run(tool.ainvoke({"purpose": "research"}))

        self.assertEqual(tool.name, "route_payment_intent")
        self.assertEqual(result["ok"], True)
        self.assertEqual(calls, [("route_payment_intent", {"purpose": "research"})])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH='agent'; python -m unittest agent.tests.test_tool_schema
```

Expected: FAIL because `tool_schema.py` does not exist.

- [ ] **Step 3: Implement generic schema conversion**

Create `agent/tool_schema.py`:

```python
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


def args_model_from_json_schema(tool_name: str, schema: dict[str, Any]) -> type[BaseModel]:
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


def make_mcp_structured_tool(metadata: McpToolMetadata, invoker: ToolInvoker) -> StructuredTool:
    async def invoke(**kwargs: Any) -> dict[str, Any]:
        return await invoker(metadata.name, kwargs)

    return StructuredTool.from_function(
        name=metadata.name,
        description=metadata.description,
        args_schema=args_model_from_json_schema(metadata.name, metadata.input_schema),
        coroutine=invoke,
    )
```

- [ ] **Step 4: Run schema tests**

Run:

```powershell
$env:PYTHONPATH='agent'; python -m unittest agent.tests.test_tool_schema
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add agent/tool_schema.py agent/tests/test_tool_schema.py
git commit -m "feat: convert mcp schemas to structured tools"
```

---

## Task 4: Add Generic MCP Runtime

**Files:**
- Create: `agent/mcp_runtime.py`
- Modify: `agent/main.py`
- Test: `agent/tests/test_mcp_runtime.py`

- [ ] **Step 1: Write failing runtime tests**

Create `agent/tests/test_mcp_runtime.py`:

```python
import asyncio
import unittest

from mcp_runtime import McpRuntime
from mcp_tool_metadata import McpToolMetadata
from skill_loader import McpServerDefinition, SkillCatalog, SkillDefinition


class FakeClient:
    def __init__(self, tools):
        self.tools = tools
        self.calls = []

    async def describe_tools(self):
        return self.tools

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return {"isError": False, "value": {"tool": tool_name, "arguments": arguments}}


class McpRuntimeTests(unittest.TestCase):
    def test_runtime_exposes_only_skill_allowlisted_tools(self) -> None:
        client = FakeClient(
            [
                McpToolMetadata("route_payment_intent", "Route payments.", {"type": "object"}),
                McpToolMetadata("internal_debug_tool", "Debug.", {"type": "object"}),
            ]
        )
        catalog = SkillCatalog(
            skills=(
                SkillDefinition(
                    name="payment-routing",
                    description="",
                    mcp_tools={"wallet-ledger-payment": ("route_payment_intent",)},
                    hidden_mcp_tools={"wallet-ledger-payment": ("internal_debug_tool",)},
                    mcp_servers={
                        "wallet-ledger-payment": McpServerDefinition(
                            name="wallet-ledger-payment",
                            url_env="WALLET_LEDGER_PAYMENT_MCP_URL",
                            tools=("route_payment_intent",),
                            hidden_tools=("internal_debug_tool",),
                        )
                    },
                ),
            )
        )
        runtime = McpRuntime(catalog, client_factory=lambda _server, _url: client)

        tools = asyncio.run(runtime.discover_tools({"WALLET_LEDGER_PAYMENT_MCP_URL": "http://wallet/mcp/"}))

        self.assertEqual([tool.name for tool in tools], ["route_payment_intent"])
        self.assertEqual(runtime.health()["wallet-ledger-payment"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH='agent'; python -m unittest agent.tests.test_mcp_runtime
```

Expected: FAIL because `mcp_runtime.py` does not exist.

- [ ] **Step 3: Implement MCP runtime**

Create `agent/mcp_runtime.py`:

```python
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from langchain_core.tools import StructuredTool

from chain_mcp_client import ChainMcpClient
from mcp_tool_metadata import McpToolMetadata
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
        self._health: dict[str, dict[str, Any]] = {}

    async def discover_tools(self, environ: Mapping[str, str]) -> list[StructuredTool]:
        tools: list[StructuredTool] = []
        for server in sorted(self._catalog.server_names()):
            url_env = self._catalog.server_url_env(server)
            url = environ.get(url_env or "")
            if not url:
                self._health[server] = {
                    "status": "degraded",
                    "error": f"{url_env} is not configured",
                }
                continue

            client = self._client_factory(server, url)
            self._clients[server] = client
            try:
                metadata = await client.describe_tools()
            except Exception as error:
                self._health[server] = {"status": "degraded", "error": str(error)}
                continue

            allowed = self._catalog.exposed_mcp_tools(server)
            hidden = self._catalog.hidden_mcp_tools_for(server)
            for item in metadata:
                if item.name in hidden:
                    continue
                if allowed and item.name not in allowed:
                    continue
                tools.append(make_mcp_structured_tool(item, self.call_tool))
            self._health[server] = {"status": "ok", "toolCount": len(tools)}
        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        for server, client in self._clients.items():
            allowed = self._catalog.exposed_mcp_tools(server)
            if tool_name in allowed:
                result = await client.call_tool(tool_name, arguments)
                if isinstance(result, dict) and result.get("isError"):
                    return {"tool": tool_name, "isError": True, "error": result}
                return {"tool": tool_name, "result": result}
        return {"tool": tool_name, "isError": True, "error": "tool is not exposed"}

    def health(self) -> dict[str, dict[str, Any]]:
        return dict(self._health)
```

If `ChainMcpClient` is too chain-named for generic use, rename it in a follow-up task after this test passes. Do not expand scope in this task.

- [ ] **Step 4: Run runtime tests**

Run:

```powershell
$env:PYTHONPATH='agent'; python -m unittest agent.tests.test_mcp_runtime agent.tests.test_tool_schema
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add agent/mcp_runtime.py agent/tests/test_mcp_runtime.py
git commit -m "feat: add skill declared mcp runtime"
```

---

## Task 5: Replace Agent Local Domain Registries With MCP Runtime

**Files:**
- Modify: `agent/main.py`
- Modify: `agent/tests/test_main_tools.py`
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Write failing clean tool exposure test**

Add to `agent/tests/test_main_tools.py`:

```python
def test_build_tools_uses_mcp_runtime_not_local_domain_registries(self) -> None:
    class FakeRuntime:
        async def discover_tools(self, environ):
            return [_make_test_tool("route_payment_intent")]

        def health(self):
            return {"wallet-ledger-payment": {"status": "ok"}}

    with patch.object(main, "get_mcp_runtime", return_value=FakeRuntime()):
        main.clear_discovered_tool_cache()
        tools = main.build_tools()

    self.assertEqual({tool.name for tool in tools}, {"route_payment_intent"})
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
docker compose --env-file C:\Users\diand\code\OntologyAgent\.env exec -T agent sh -c "PYTHONPATH=agent python -m unittest tests.test_main_tools"
```

Expected: FAIL because `get_mcp_runtime()` is not wired into `build_tools()`.

- [ ] **Step 3: Modify `main.py` to use runtime**

In `agent/main.py`, add:

```python
from mcp_runtime import McpRuntime


@lru_cache(maxsize=1)
def get_mcp_runtime() -> McpRuntime:
    return McpRuntime(get_skill_catalog())
```

Replace local registry assembly in `build_tools()` with:

```python
def build_tools() -> list[StructuredTool]:
    if _discovered_chain_tools is not None or _discovered_freqtrade_tools is not None:
        return [
            *_load_discovered_chain_tools(),
            *_load_discovered_freqtrade_tools(),
        ]
    if _in_running_loop():
        return []
    try:
        return asyncio.run(get_mcp_runtime().discover_tools(os.environ))
    except Exception as error:
        logger.debug("Failed to discover MCP runtime tools: %s", error)
        return []
```

Then remove from `main.py`:

- `CHAIN_TOOL_REGISTRY`
- `FREQTRADE_TOOL_REGISTRY`
- `PAYMENT_ROUTER_TOOL_REGISTRY`
- `LEDGER_TOOL_REGISTRY`
- `LOCAL_TOOL_REGISTRY`
- local domain tool wrapper imports

Keep temporary legacy cache helpers only if needed by existing tests. If a helper only exists for legacy chain/freqtrade discovery, remove it in Task 8.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
docker compose --env-file C:\Users\diand\code\OntologyAgent\.env exec -T agent sh -c "PYTHONPATH=agent python -m unittest tests.test_main_tools tests.test_mcp_runtime tests.test_tool_schema"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add agent/main.py agent/tests/test_main_tools.py
git commit -m "refactor: expose tools through skill mcp runtime"
```

---

## Task 6: Create `wallet-ledger-payment` MCP Service

**Files:**
- Create: `wallet-ledger-payment/server.py`
- Create: `wallet-ledger-payment/payment_router.py`
- Create: `wallet-ledger-payment/ledger_store.py`
- Create: `wallet-ledger-payment/tests/test_payment_router.py`
- Create: `wallet-ledger-payment/tests/test_mcp_tools.py`
- Create: `wallet-ledger-payment/requirements.txt`
- Create: `wallet-ledger-payment/Dockerfile`

- [ ] **Step 1: Write payment router tests**

Create `wallet-ledger-payment/tests/test_payment_router.py`:

```python
import unittest

from payment_router import PaymentIntent, route_payment_intent


class PaymentRouterTests(unittest.TestCase):
    def test_routes_async_task_to_ledger_escrow(self) -> None:
        decision = route_payment_intent(
            PaymentIntent(
                purpose="research task",
                deliveryMode="async_task",
                requiresAcceptance=True,
                externalService=False,
            )
        )

        self.assertEqual(decision["method"], "ledger_escrow")
        self.assertEqual(
            decision["allowedTools"],
            [
                "agent_wallet_create_escrow",
                "agent_wallet_release_escrow",
                "agent_wallet_refund_escrow",
            ],
        )

    def test_routes_immediate_external_api_to_x402(self) -> None:
        decision = route_payment_intent(
            PaymentIntent(
                purpose="paid api",
                deliveryMode="immediate_api",
                requiresAcceptance=False,
                externalService=True,
                serviceUrl="https://seller.example/x402",
            )
        )

        self.assertEqual(decision["method"], "x402")
        self.assertEqual(decision["allowedTools"], ["chain_x402_fetch"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m unittest discover -s wallet-ledger-payment/tests
```

Expected: FAIL because `wallet-ledger-payment/payment_router.py` does not exist.

- [ ] **Step 3: Implement payment router**

Copy the existing domain logic from `agent/payment_router.py` into `wallet-ledger-payment/payment_router.py`, keeping the public names:

```python
PaymentIntent
PaymentRouteDecision
route_payment_intent
```

Do not import from `agent`.

- [ ] **Step 4: Write MCP tool tests**

Create `wallet-ledger-payment/tests/test_mcp_tools.py`:

```python
import asyncio
import unittest

from server import route_payment_intent_tool


class WalletLedgerPaymentMcpTests(unittest.TestCase):
    def test_route_payment_intent_tool_returns_decision(self) -> None:
        result = asyncio.run(
            route_payment_intent_tool(
                purpose="paid api",
                deliveryMode="immediate_api",
                requiresAcceptance=False,
                externalService=True,
                serviceUrl="https://seller.example/x402",
            )
        )

        self.assertEqual(result["method"], "x402")
        self.assertEqual(result["allowedTools"], ["chain_x402_fetch"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5: Implement initial MCP server tool module**

Create `wallet-ledger-payment/server.py` with at least the tool functions. If full MCP transport setup requires more repo-specific work, keep the callable functions testable first:

```python
from __future__ import annotations

from typing import Literal, Optional

from pydantic import HttpUrl

from payment_router import PaymentIntent, route_payment_intent


async def route_payment_intent_tool(
    purpose: str,
    deliveryMode: Literal["async_task", "immediate_api", "withdrawal", "unknown"] = "unknown",
    requiresAcceptance: bool = False,
    externalService: bool = False,
    serviceUrl: Optional[HttpUrl] = None,
) -> dict[str, object]:
    return route_payment_intent(
        PaymentIntent(
            purpose=purpose,
            deliveryMode=deliveryMode,
            requiresAcceptance=requiresAcceptance,
            externalService=externalService,
            serviceUrl=serviceUrl,
        )
    )
```

- [ ] **Step 6: Run wallet-ledger-payment tests**

Run:

```powershell
$env:PYTHONPATH='wallet-ledger-payment'; python -m unittest discover -s wallet-ledger-payment/tests
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add wallet-ledger-payment
git commit -m "feat: add wallet ledger payment mcp service"
```

---

## Task 7: Wire Compose and Skill Manifests to External MCP Services

**Files:**
- Modify: `docker-compose.yml`
- Modify: `agent/skills/payment-routing/skill.json`
- Modify: `agent/skills/ledger-escrow/skill.json`
- Modify: `agent/skills/agent-wallet/skill.json`
- Modify: `agent/skills/chain-wallet/skill.json`
- Modify: `agent/skills/freqtrade/skill.json`

- [ ] **Step 1: Update Docker Compose**

Add a new service:

```yaml
  wallet-ledger-payment:
    build:
      context: ./wallet-ledger-payment
    container_name: wallet-ledger-payment
    ports:
      - "8093:8093"
    environment:
      LEDGER_STATE_PATH: ${LEDGER_STATE_PATH:-/app/data/offchain_ledger.json}
    volumes:
      - ./ledger/data:/app/data
    restart: unless-stopped
```

Add to `agent.environment`:

```yaml
      WALLET_LEDGER_PAYMENT_MCP_URL: ${WALLET_LEDGER_PAYMENT_MCP_URL:-http://wallet-ledger-payment:8093/mcp/}
```

Add `wallet-ledger-payment` to `agent.depends_on`.

- [ ] **Step 2: Update skill manifests to v2**

Use this shape for `agent/skills/payment-routing/skill.json`:

```json
{
  "name": "payment-routing",
  "enabled": true,
  "description": "Route payment intents before settlement actions.",
  "instructions": "instructions.md",
  "mcpServers": {
    "wallet-ledger-payment": {
      "urlEnv": "WALLET_LEDGER_PAYMENT_MCP_URL",
      "tools": ["route_payment_intent"]
    }
  },
  "hiddenTools": {
    "wallet-ledger-payment": []
  }
}
```

Use this shape for `agent/skills/chain-wallet/skill.json`:

```json
{
  "name": "chain-wallet",
  "enabled": true,
  "description": "Use chain MCP tools for chain actions.",
  "instructions": "instructions.md",
  "mcpServers": {
    "chain": {
      "urlEnv": "CHAIN_MCP_URL",
      "tools": [
        "chain_get_wallet_state",
        "chain_sign_transfer",
        "chain_submit_execution",
        "chain_submit_user_operation",
        "chain_get_transaction_receipt",
        "chain_get_user_operation_status",
        "chain_x402_fetch",
        "chain_execute_trade_intent"
      ]
    }
  },
  "hiddenTools": {
    "chain": []
  }
}
```

Update `freqtrade`, `ledger-escrow`, and `agent-wallet` the same way using their existing tool lists and server names.

- [ ] **Step 3: Run compose config validation**

Run:

```powershell
docker compose --env-file C:\Users\diand\code\OntologyAgent\.env config
```

Expected: command exits 0 and shows `wallet-ledger-payment`.

- [ ] **Step 4: Run skill tests**

Run:

```powershell
docker compose --env-file C:\Users\diand\code\OntologyAgent\.env exec -T agent sh -c "PYTHONPATH=agent python -m unittest tests.test_skill_loader tests.test_main_tools"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add docker-compose.yml agent/skills
git commit -m "feat: declare domain capabilities via skill mcp servers"
```

---

## Task 8: Remove Agent Wallet UI/API and Enforce Clean Core

**Files:**
- Modify: `agent/main.py`
- Modify: `agent/web/chat.html`
- Delete: `agent/agent_wallet_auth.py`
- Delete: `agent/agent_wallet_state.py`
- Delete: `agent/agent_wallet_flows.py`
- Delete: `agent/chain_wallet_tools.py`
- Delete: `agent/payment_ledger_tools.py`
- Delete: `agent/payment_router.py`
- Delete or shrink: `agent/tool_models.py`
- Modify: `agent/tests/test_main_api.py`
- Modify: `agent/tests/test_main_tools.py`
- Create: `agent/tests/test_clean_core.py`

- [ ] **Step 1: Write clean-core import guard test**

Create `agent/tests/test_clean_core.py`:

```python
import ast
from pathlib import Path
import unittest


BANNED_IMPORTS = {
    "agent_wallet_auth",
    "agent_wallet_state",
    "agent_wallet_flows",
    "chain_wallet_tools",
    "payment_ledger_tools",
    "payment_router",
    "ledger_client",
}


class CleanCoreTests(unittest.TestCase):
    def test_agent_core_does_not_import_domain_modules(self) -> None:
        core_files = [
            Path("agent/main.py"),
            Path("agent/mcp_runtime.py"),
            Path("agent/prompt_builder.py"),
            Path("agent/tool_schema.py"),
            Path("agent/skill_loader.py"),
        ]
        violations = []
        for path in core_files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] in BANNED_IMPORTS:
                            violations.append(f"{path}:{alias.name}")
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.split(".")[0] in BANNED_IMPORTS:
                        violations.append(f"{path}:{node.module}")

        self.assertEqual(violations, [])

    def test_agent_core_has_no_agent_wallet_routes(self) -> None:
        source = Path("agent/main.py").read_text(encoding="utf-8")
        self.assertNotIn("/agent-wallet/", source)
        self.assertNotIn("agent_wallet_", source)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH='agent'; python -m unittest agent.tests.test_clean_core
```

Expected: FAIL until domain imports and routes are removed.

- [ ] **Step 3: Remove Agent Wallet UI/API from `main.py`**

Delete routes and helpers that only support wallet UI/API:

- `/auth/session`
- `/auth/github/login`
- `/auth/github/callback`
- `/auth/logout`
- `/agent-wallet/state`
- `/agent-wallet/init`
- `/agent-wallet/claim`
- `/agent-wallet/register-service`
- `/agent-wallet/call-service`
- `/agent-wallet/reset`
- GitHub OAuth helper functions
- Agent Wallet state store cache

Keep generic chat/session/runtime endpoints only.

- [ ] **Step 4: Remove Agent Wallet panel from `chat.html`**

Delete the HTML section with `id="agent-wallet-panel"` and delete JavaScript variables/functions that reference:

```text
walletAuthStateEl
walletStateSummaryEl
walletSignInButtonEl
walletSignOutButtonEl
walletClaimFormEl
walletClaimCodeEl
walletApi
refreshAgentWalletState
claimAgentWallet
signOutWalletOwner
state.wallet
```

Keep generic chat, runtime health, chain status, freqtrade status, and autonomy dashboard only if they are generic enough for current runtime. If a dashboard panel depends on domain-specific payloads, remove it or make it render generic health only.

- [ ] **Step 5: Delete domain modules from `agent`**

Remove files after no imports remain:

```powershell
git rm agent/agent_wallet_auth.py agent/agent_wallet_state.py agent/agent_wallet_flows.py
git rm agent/chain_wallet_tools.py agent/payment_ledger_tools.py agent/payment_router.py
```

If `agent/tool_models.py` contains only generic chat/session models after cleanup, keep it. If it contains only deleted domain schemas, remove it:

```powershell
git rm agent/tool_models.py
```

- [ ] **Step 6: Update tests**

Remove or move tests that target deleted Agent Wallet API endpoints from `agent/tests/test_main_api.py`.

Keep tests for:

- generic chat sessions
- streaming fallback
- `/health`
- dynamic skill tool exposure
- clean-core import guard

Move payment routing tests to `wallet-ledger-payment/tests/test_payment_router.py`.

- [ ] **Step 7: Run focused tests**

Run:

```powershell
$env:PYTHONPATH='agent'; python -m unittest agent.tests.test_clean_core agent.tests.test_skill_loader agent.tests.test_mcp_runtime agent.tests.test_tool_schema
```

Expected: PASS.

Run:

```powershell
$env:PYTHONPATH='wallet-ledger-payment'; python -m unittest discover -s wallet-ledger-payment/tests
```

Expected: PASS.

- [ ] **Step 8: Rebuild and health check**

Run:

```powershell
docker compose --env-file C:\Users\diand\code\OntologyAgent\.env up -d --build agent wallet-ledger-payment
curl.exe http://localhost:8000/health
```

Expected: `agent` reports `status: ok`; skill MCP health includes configured servers; no `/agent-wallet/*` routes exist.

- [ ] **Step 9: Commit**

```powershell
git add agent wallet-ledger-payment docker-compose.yml
git commit -m "refactor: make agent core domain free"
```

---

## Self-Review

Spec coverage:

- Pure Agent Core with no financial-domain implementation: Task 5 and Task 8.
- Dynamic skill-loaded MCP servers: Task 1, Task 4, Task 7.
- Prompt from skill instructions only: Task 2.
- MCP schema conversion: Task 3.
- New `wallet-ledger-payment` MCP: Task 6.
- Remove Agent Wallet UI/API: Task 8.
- Health and degraded server behavior: Task 4 and Task 7.
- Clean-core enforcement: Task 8.

Placeholder scan:

- No `TBD`, `TODO`, or "implement later" placeholders are intentionally present.
- Each task has exact files, commands, and expected outcomes.

Type consistency:

- `SkillCatalog.server_names()` and `SkillCatalog.server_url_env()` are introduced in Task 1 before use in Task 4.
- `make_mcp_structured_tool()` is introduced in Task 3 before use in Task 4.
- `McpRuntime` is introduced in Task 4 before integration in Task 5.
