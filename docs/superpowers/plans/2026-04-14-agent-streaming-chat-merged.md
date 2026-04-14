# Agent Streaming Chat Implementation Plan (Merged)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement streaming-only session chat by adding the `/messages/stream` endpoint and removing the old synchronous `/messages` endpoint. The browser chat UI should use only the stream endpoint with incremental rendering.

**Architecture:** Single write path via SSE-style events over `POST /agent/sessions/{id}/messages/stream`. Remove the old synchronous route. Persist only the final assembled assistant message from the streaming path. Browser handles `start`, `delta`, `final`, `error` events.

**Tech Stack:** Python 3, FastAPI, `StreamingResponse`, LangGraph/LangChain, HTML, vanilla JavaScript, `fastapi.testclient`, `pytest`, `unittest`

---

## File Map

- Modify: `agent/main.py`
  - Add streaming endpoint, remove old sync endpoint
- Modify: `agent/tests/test_main_api.py`
  - Add stream tests, replace sync expectations with removal tests
- Modify: `agent/web/chat.html`
  - Use streaming-only, add placeholder/delta rendering

## Task 1: Add Failing Streaming Endpoint Test

**Files:**
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Write the failing streaming endpoint test**

```python
class StreamingGraph:
    async def astream(self, payload: dict[str, object], stream_mode: str = "messages"):
        yield AIMessageChunk(content="你")
        yield AIMessageChunk(content="好")
```

```python
def test_agent_session_stream_emits_start_delta_final_events(self) -> None:
    controller = FakeAutonomyController()
    with (
        patch.object(main, "get_autonomy_controller", return_value=controller),
        patch.object(main, "get_agent_graph", return_value=StreamingGraph()),
    ):
        with TestClient(main.app) as client:
            create_response = client.post("/agent/sessions")
            session_id = create_response.json()["sessionId"]
            response = client.post(
                f"/agent/sessions/{session_id}/messages/stream",
                json={"input": "你好"},
            )

    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.headers["content-type"].split(";")[0], "text/event-stream")
    body = response.text
    self.assertIn("event: start", body)
    self.assertIn('event: delta', body)
    self.assertIn('"text": "你"', body)
    self.assertIn('"text": "好"', body)
    self.assertIn("event: final", body)
    self.assertIn('"output": "你好"', body)
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_main_api.py -k stream_emits_start_delta_final_events -v`
Expected: FAIL because the `/messages/stream` endpoint does not exist yet

## Task 2: Implement The Streaming Session Endpoint

**Files:**
- Modify: `agent/main.py`

- [ ] **Step 1: Add minimal SSE helper formatting**

```python
def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

- [ ] **Step 2: Add the streaming endpoint and backend accumulator**

```python
@app.post("/agent/sessions/{session_id}/messages/stream")
async def stream_agent_session_message(session_id: str, request: AgentChatRequest) -> StreamingResponse:
    ...
```

Behavior:
- Load session or return 404
- Build prompt messages with new user turn
- Stream graph output incrementally
- Emit `start` once
- Emit `delta` for each non-empty assistant chunk
- Accumulate final text in memory
- On success: persist one final assistant message, emit `final` with sessionId, output, messageCount
- On failure: do not persist, emit `error`

```python
async def _stream_agent_reply(messages: list[Any]) -> AsyncIterator[tuple[str, str]]:
    graph = get_agent_graph()
    async for chunk in graph.astream({"messages": messages}, stream_mode="messages"):
        text = _normalize_message_content(getattr(chunk, "content", ""))
        if text:
            yield "delta", text
```

```python
async def event_generator() -> AsyncIterator[str]:
    yield _sse_event("start", {"sessionId": session.session_id, "input": request.input})
    collected: list[str] = []
    try:
        async for event_name, text in _stream_agent_reply([...]):
            collected.append(text)
            yield _sse_event(event_name, {"text": text})

        output = "".join(collected) or EMPTY_FINAL_OUTPUT_FALLBACK
        session.messages = [*session.messages, HumanMessage(content=request.input), AIMessage(content=output)]
        yield _sse_event("final", {"sessionId": session.session_id, "output": output, "messageCount": len(session.messages)})
    except Exception as error:
        yield _sse_event("error", {"message": str(error)})
```

- [ ] **Step 3: Run the focused test to verify it passes**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_main_api.py -k stream_emits_start_delta_final_events -v`
Expected: PASS

- [ ] **Step 4: Add more backend regression tests**

```python
def test_agent_session_stream_persists_final_output(self) -> None:
    ...
    state_response = client.get(f"/agent/sessions/{session_id}")
    self.assertEqual(state_response.json()["messageCount"], 2)
```

```python
def test_agent_session_stream_emits_error_without_persisting_partial_reply(self) -> None:
    ...
    self.assertIn("event: error", response.text)
    self.assertEqual(state_response.json()["messageCount"], 0)
```

- [ ] **Step 5: Run the focused stream tests to verify they pass**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_main_api.py -k "agent_session_stream" -v`
Expected: PASS

## Task 3: Write Test For Removed Synchronous Endpoint

**Files:**
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Write the test for removed sync endpoint**

```python
def test_agent_session_sync_endpoint_is_removed(self) -> None:
    controller = FakeAutonomyController()
    with patch.object(main, "get_autonomy_controller", return_value=controller):
        with TestClient(main.app) as client:
            create_response = client.post("/agent/sessions")
            session_id = create_response.json()["sessionId"]
            response = client.post(
                f"/agent/sessions/{session_id}/messages",
                json={"input": "hello"},
            )

    self.assertEqual(response.status_code, 404)
```

- [ ] **Step 2: Run the test to verify sync endpoint still exists (should fail)**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_main_api.py -k sync_endpoint_is_removed -v`
Expected: FAIL because the old sync route still exists

## Task 4: Remove The Old Synchronous Session-Message Route

**Files:**
- Modify: `agent/main.py`

- [ ] **Step 1: Remove the old route**

Delete from `agent/main.py`:

```python
@app.post("/agent/sessions/{session_id}/messages")
async def send_agent_session_message(...):
    ...
```

- [ ] **Step 2: Verify streaming route remains**

The streaming route should still be:

```python
@app.post("/agent/sessions/{session_id}/messages/stream")
async def stream_agent_session_message(...):
    ...
```

- [ ] **Step 3: Run the focused removal test to verify it passes**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_main_api.py -k sync_endpoint_is_removed -v`
Expected: PASS

- [ ] **Step 4: Re-run stream endpoint tests**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_main_api.py -k agent_session_stream -v`
Expected: PASS

## Task 5: Add Front-End Streaming Placeholder And Delta Rendering

**Files:**
- Modify: `agent/web/chat.html`
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Write the failing front-end marker test**

```python
def test_chat_page_declares_streaming_chat_helpers(self) -> None:
    controller = FakeAutonomyController()
    with patch.object(main, "get_autonomy_controller", return_value=controller):
        with TestClient(main.app) as client:
            response = client.get("/")

    self.assertIn("async function streamAgentMessage", response.text)
    self.assertIn("function appendOrUpdateStreamingAgentMessage", response.text)
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_main_api.py -k streaming_chat_helpers -v`
Expected: FAIL because the page does not yet contain these helpers

- [ ] **Step 3: Add streaming front-end helpers**

```javascript
function appendOrUpdateStreamingAgentMessage(messageId, text) {
  let node = document.querySelector(`[data-stream-message-id="${messageId}"]`);
  if (!node) {
    node = addMessage("agent", "Agent", "");
    node.dataset.streamMessageId = messageId;
  }
  node.querySelector(".message-content").textContent = text;
  return node;
}

async function streamAgentMessage(sessionId, input) {
  const response = await fetch(`/agent/sessions/${sessionId}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input }),
  });
  ...
}
```

Stream parser should:
- Parse `event:` and `data:` blocks
- Create a placeholder on `start`
- Append text on `delta`
- Finalize on `final`
- Show error state on `error`

- [ ] **Step 4: Rewire `sendMessage()` to use streaming**

- Add user bubble
- Clear composer
- Call `streamAgentMessage(sessionId, input)` instead of requestJson(.../messages)
- Keep busy handling and dashboard refresh pattern

- [ ] **Step 5: Run the focused page test to verify it passes**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_main_api.py -k streaming_chat_helpers -v`
Expected: PASS

## Task 6: Tighten Front-End To Streaming-Only Semantics

**Files:**
- Modify: `agent/web/chat.html`
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Add test that chat UI uses only stream endpoint**

```python
def test_chat_page_uses_stream_endpoint_for_session_messages(self) -> None:
    controller = FakeAutonomyController()
    with patch.object(main, "get_autonomy_controller", return_value=controller):
        with TestClient(main.app) as client:
            response = client.get("/")

    self.assertIn("/messages/stream", response.text)
    self.assertNotIn("/messages\"", response.text)
```

- [ ] **Step 2: Run the focused test to verify it fails if any old sync-path reference remains**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_main_api.py -k uses_stream_endpoint_for_session_messages -v`
Expected: FAIL if the page source still contains old sync endpoint references

- [ ] **Step 3: Remove dead sync-path assumptions from `chat.html`**

Ensure:
- `sendMessage()` only calls `streamAgentMessage(sessionId, input)`
- No fallback request to `/agent/sessions/${sessionId}/messages`
- Comments, helper names, and error text no longer imply a sync fallback exists

- [ ] **Step 4: Re-run the focused page-source test to verify it passes**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_main_api.py -k uses_stream_endpoint_for_session_messages -v`
Expected: PASS

## Task 7: Add Browser-Contract Tests For Placeholder, Finalization, And Error State

**Files:**
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Add failing page-source contract test for streaming UI markers**

```python
def test_chat_page_contains_streaming_placeholder_and_error_markers(self) -> None:
    controller = FakeAutonomyController()
    with patch.object(main, "get_autonomy_controller", return_value=controller):
        with TestClient(main.app) as client:
            response = client.get("/")

    self.assertIn("data-stream-message-id", response.text)
    self.assertIn("stream-error", response.text)
    self.assertIn("event: final", response.text)
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_main_api.py -k streaming_placeholder_and_error_markers -v`
Expected: FAIL until the front-end contains the streaming placeholder/error handling markers

- [ ] **Step 3: Add the smallest page marker coverage needed**

Suggested additions to `chat.html`:
- A `stream-error` CSS class or status marker
- A clear placeholder update path in the script

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_main_api.py -k streaming_placeholder_and_error_markers -v`
Expected: PASS

## Task 8: Final Verification

**Files:**
- Modify: none expected unless verification reveals a real issue

- [ ] **Step 1: Run the main API test file**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_main_api.py -v`
Expected: PASS

- [ ] **Step 2: Run the broader nearby suite**

Run: `PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_autonomy.py agent/tests/test_autonomy_workflows.py agent/tests/test_main_api.py -q`
Expected: PASS

- [ ] **Step 3: Inspect git status for intended files only**

Run: `git status --short`
Expected: only these files are modified if no extra fixes were needed:
- `agent/main.py`
- `agent/web/chat.html`
- `agent/tests/test_main_api.py`

- [ ] **Step 4: Commit verification-only fixes if needed**

```bash
git add agent/main.py agent/web/chat.html agent/tests/test_main_api.py
git commit -m "test: verify streaming-only agent chat"
```

## Self-Review

### Spec Coverage

- streaming endpoint added: Tasks 1 and 2
- old sync endpoint removed: Tasks 3 and 4
- front-end streaming-only: Tasks 5 and 6
- placeholder/delta rendering: Tasks 5 and 7
- error-state UI: Tasks 5, 6, and 7
- final session consistency: Tasks 2 and 7

No spec requirement is left without a matching implementation task.

### Placeholder Scan

- no `TODO` or `TBD`
- exact files are named
- exact commands and expected outcomes are included

### Type Consistency

- the canonical endpoint is `POST /agent/sessions/{session_id}/messages/stream`
- event names are consistently `start`, `delta`, `final`, and `error`
- front-end helper names: `streamAgentMessage` and `appendOrUpdateStreamingAgentMessage`