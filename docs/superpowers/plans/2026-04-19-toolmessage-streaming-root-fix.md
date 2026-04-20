# ToolMessage Streaming Root Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace accidental `ToolMessage`-recovery behavior with an explicit provider-aware execution mode that disables streamed tool execution for the current OpenAI-compatible provider and uses stable non-streaming agent invocation for those turns.

**Architecture:** Add a small provider capability decision in `agent/main.py`, route the session stream endpoint through `ainvoke()` directly when streamed tool execution is unsupported for the configured provider, and keep the current `ToolMessage` fallback code only as defense in depth for providers that still use `astream()`. Preserve the existing SSE API by continuing to emit `start` and `final` events even when token-by-token tool streaming is bypassed.

**Tech Stack:** Python (`fastapi`, `langgraph`, `langchain-openai`, `unittest`), SSE streaming in FastAPI

---

## File Map

- Modify: `agent/main.py`
  Responsibility: add provider capability detection and route stream requests through the stable invocation path when streamed tool execution is unsupported.
- Modify: `agent/tests/test_main_api.py`
  Responsibility: verify stream endpoint chooses direct `ainvoke()` mode for the known provider and no longer depends on malformed `ToolMessage` recovery in that mode.
- Modify: `agent/tests/test_main_tools.py`
  Responsibility: add a small unit-level assertion for the provider capability helper if the implementation exposes one.

## Implementation Notes

- Current known unstable provider:
  - `OPENAI_BASE_URL=https://www.packyapi.com/v1`
- V1 behavior for that provider:
  - `/agent/sessions/{session_id}/messages/stream` emits `start`
  - executes the turn with `graph.ainvoke()` directly
  - emits `final`
  - does not attempt `graph.astream()` for that turn
- Existing `ToolMessage` validation fallback and session sanitization remain in place as defense in depth for non-gated providers.

### Task 1: Add Provider Capability Detection

**Files:**
- Modify: `agent/main.py`
- Modify: `agent/tests/test_main_tools.py`

- [ ] **Step 1: Add a failing helper test in `agent/tests/test_main_tools.py`**

Append:

```python
    def test_streamed_tool_execution_is_disabled_for_packyapi(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_BASE_URL": "https://www.packyapi.com/v1"},
            clear=False,
        ):
            self.assertFalse(main._provider_supports_streamed_tool_execution())

    def test_streamed_tool_execution_defaults_to_enabled_without_known_provider_gate(self) -> None:
        with patch.dict("os.environ", {"OPENAI_BASE_URL": "https://api.openai.com/v1"}, clear=False):
            self.assertTrue(main._provider_supports_streamed_tool_execution())
```

- [ ] **Step 2: Run the focused helper tests to verify they fail**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_main_tools.MainToolRegistryTests.test_streamed_tool_execution_is_disabled_for_packyapi agent.tests.test_main_tools.MainToolRegistryTests.test_streamed_tool_execution_defaults_to_enabled_without_known_provider_gate`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: FAIL because the helper does not exist yet.

- [ ] **Step 3: Add the capability helper in `agent/main.py`**

Add near the other provider/config helpers:

```python
def _provider_supports_streamed_tool_execution() -> bool:
    base_url = get_openai_base_url()
    if not base_url:
        return True
    normalized = base_url.rstrip("/").lower()
    if normalized == "https://www.packyapi.com/v1":
        return False
    return True
```

- [ ] **Step 4: Run the focused helper tests again**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_main_tools.MainToolRegistryTests.test_streamed_tool_execution_is_disabled_for_packyapi agent.tests.test_main_tools.MainToolRegistryTests.test_streamed_tool_execution_defaults_to_enabled_without_known_provider_gate`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/main.py agent/tests/test_main_tools.py
git commit -m "feat: add provider gate for streamed tool execution"
```

### Task 2: Route Stream Sessions Through `ainvoke()` for Gated Providers

**Files:**
- Modify: `agent/main.py`
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Add a failing stream-mode routing test in `agent/tests/test_main_api.py`**

Append:

```python
class PackyInvokeOnlyGraph:
    def __init__(self) -> None:
        self.astream_calls = 0
        self.ainvoke_calls = 0

    async def astream(self, payload: dict[str, object], stream_mode: str):
        self.astream_calls += 1
        raise AssertionError("astream should not be used for provider-gated streamed tool execution")
        yield  # pragma: no cover

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        self.ainvoke_calls += 1
        messages = list(payload["messages"])  # type: ignore[index]
        messages.append(AIMessage(content="provider gated final answer"))
        return {"messages": messages}


    def test_agent_session_stream_uses_direct_invoke_for_packyapi_provider(self) -> None:
        controller = FakeAutonomyController()
        graph = PackyInvokeOnlyGraph()

        with (
            patch.dict("os.environ", {"OPENAI_BASE_URL": "https://www.packyapi.com/v1"}, clear=False),
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=graph),
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                session_id = create_response.json()["sessionId"]
                with client.stream(
                    "POST",
                    f"/agent/sessions/{session_id}/messages/stream",
                    json={"input": "查看理财子状态，然后给我一个建议"},
                ) as response:
                    body = "".join(response.iter_text())

        self.assertIn("event: start", body)
        self.assertIn("event: final", body)
        self.assertIn('"output": "provider gated final answer"', body)
        self.assertNotIn("event: error", body)
        self.assertEqual(graph.astream_calls, 0)
        self.assertEqual(graph.ainvoke_calls, 1)
```

- [ ] **Step 2: Run the focused stream-mode routing test to verify it fails**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_main_api.MainApiTests.test_agent_session_stream_uses_direct_invoke_for_packyapi_provider`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: FAIL because the stream endpoint still calls `astream()` first.

- [ ] **Step 3: Update `stream_agent_session_message()` in `agent/main.py`**

Add a provider-gated branch before entering the streamed tool path:

```python
        try:
            graph = get_agent_graph()
            if not _provider_supports_streamed_tool_execution():
                invoke_result = await graph.ainvoke({"messages": pending_messages})
                invoke_messages = invoke_result.get("messages")
                if not isinstance(invoke_messages, list):
                    yield _sse_event(
                        "error",
                        {
                            "sessionId": session.session_id,
                            "error": "Agent direct invoke returned invalid message payload",
                        },
                    )
                    return
                latest_messages = invoke_messages
            else:
                stream = graph.astream({"messages": pending_messages}, stream_mode=["messages", "values"])
                async for item in stream:
                    delta, latest_messages = _consume_stream_item(item, latest_messages)
                    if delta is not None:
                        deltas.append(delta)
                        yield _sse_event("delta", {"delta": delta})
```

Keep the existing `ToolMessage` fallback path intact for providers that still use `astream()`.

- [ ] **Step 4: Run the focused stream-mode routing test again**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_main_api.MainApiTests.test_agent_session_stream_uses_direct_invoke_for_packyapi_provider`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/main.py agent/tests/test_main_api.py
git commit -m "feat: bypass streamed tool execution for packyapi"
```

### Task 3: Preserve Existing Behavior for Non-Gated Providers

**Files:**
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Add a failing regression test for non-gated providers**

Append:

```python
class NonGatedStreamingGraph:
    def __init__(self) -> None:
        self.astream_calls = 0
        self.ainvoke_calls = 0

    async def astream(self, payload: dict[str, object], stream_mode: str):
        self.astream_calls += 1
        yield "messages", (AIMessageChunk(content="你好"), {"node": "agent"})
        yield "values", {"messages": [*payload["messages"], AIMessage(content="你好")]}  # type: ignore[index]

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        self.ainvoke_calls += 1
        return {"messages": [*payload["messages"], AIMessage(content="should not be used")]}  # type: ignore[index]


    def test_agent_session_stream_keeps_astream_for_non_gated_provider(self) -> None:
        controller = FakeAutonomyController()
        graph = NonGatedStreamingGraph()

        with (
            patch.dict("os.environ", {"OPENAI_BASE_URL": "https://api.openai.com/v1"}, clear=False),
            patch.object(main, "get_autonomy_controller", return_value=controller),
            patch.object(main, "get_agent_graph", return_value=graph),
        ):
            with TestClient(main.app) as client:
                create_response = client.post("/agent/sessions")
                session_id = create_response.json()["sessionId"]
                with client.stream(
                    "POST",
                    f"/agent/sessions/{session_id}/messages/stream",
                    json={"input": "你好"},
                ) as response:
                    body = "".join(response.iter_text())

        self.assertIn('"delta": "你好"', body)
        self.assertIn('"output": "你好"', body)
        self.assertEqual(graph.astream_calls, 1)
        self.assertEqual(graph.ainvoke_calls, 0)
```

- [ ] **Step 2: Run the focused non-gated regression test**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_main_api.MainApiTests.test_agent_session_stream_keeps_astream_for_non_gated_provider`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS after Task 2 is complete.

- [ ] **Step 3: Run the full stream-related API suite**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_main_api`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add agent/tests/test_main_api.py
git commit -m "test: cover provider-aware stream tool routing"
```

### Task 4: End-to-End Verification

**Files:**
- Verify only: `agent/main.py`, stream endpoint behavior, running container

- [ ] **Step 1: Run all affected agent tests**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_main_api agent.tests.test_main_tools`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 2: Rebuild or refresh the running agent service**

Run: `docker compose --env-file "/Users/freedom/cc/OntologyAgent/.env" up -d --build --no-deps agent`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: `agent` restarts successfully.

- [ ] **Step 3: Verify the real provider now uses the stable direct-invoke stream path**

Run:

```bash
python3 - <<'PY'
import json, urllib.request
req = urllib.request.Request('http://localhost:8000/agent/sessions', method='POST')
with urllib.request.urlopen(req, timeout=20) as resp:
    session_id = json.loads(resp.read().decode())['sessionId']
payload = json.dumps({'input':'查看理财子状态，然后给我一个建议'}).encode()
req = urllib.request.Request(
    f'http://localhost:8000/agent/sessions/{session_id}/messages/stream',
    data=payload,
    headers={'Content-Type':'application/json'},
)
with urllib.request.urlopen(req, timeout=120) as resp:
    print(resp.read().decode())
PY
```

Expected:

- `event:start` and `event:final` are present
- no `event:error`
- no dependence on streamed tool-call deltas for a successful turn under the gated provider

- [ ] **Step 4: Commit the final verified state**

```bash
git add agent/main.py agent/tests/test_main_api.py agent/tests/test_main_tools.py
git commit -m "feat: gate streamed tool execution by provider"
```

## Self-Review

- Spec coverage:
  - provider capability gate: Task 1
  - non-streaming execution path for the known unstable provider: Task 2
  - non-gated provider regression: Task 3
  - runtime verification against the real configured provider: Task 4
- Placeholder scan: no `TODO`, `TBD`, or vague deferred steps remain.
- Type consistency:
  - provider gating uses one helper `_provider_supports_streamed_tool_execution()`
  - the stream endpoint keeps the same SSE contract while switching execution mode internally
