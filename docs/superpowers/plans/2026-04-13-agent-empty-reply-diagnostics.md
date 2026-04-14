# Agent Empty Reply Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add targeted backend warning logs for empty final agent replies so provider-level empty-content failures can be diagnosed from server logs without changing the API contract.

**Architecture:** Keep the current fallback user-facing response and instrument only the empty-reply branch in `agent/main.py`. Use a module logger near `_extract_final_output()` to record structured diagnostics about the final AI message, provider metadata, and lightweight message-chain context. Add one focused regression test to prove that empty replies emit the warning while still returning the fallback text.

**Tech Stack:** Python 3, FastAPI, standard `logging`, `unittest`, `fastapi.testclient`, `langchain_core.messages`

---

## File Map

- Modify: `agent/main.py`
  - Add a module logger and emit a warning when final normalized agent output is empty
- Modify: `agent/tests/test_main_api.py`
  - Add regression coverage for the warning log emitted on empty model replies

## Task 1: Add A Failing Regression Test For Empty Reply Diagnostics

**Files:**
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Write the failing test**

Add a graph double that returns an empty AI reply with provider metadata:

```python
class EmptyReplyGraph:
    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        messages = list(payload["messages"])  # type: ignore[index]
        messages.append(
            AIMessage(
                content="",
                additional_kwargs={"refusal": None},
                response_metadata={
                    "model_name": "gpt-5.2",
                    "id": "chatcmpl-empty-123",
                    "finish_reason": "stop",
                },
            )
        )
        return {"messages": messages}
```

Then add a failing test like this:

```python
def test_agent_sessions_log_empty_model_reply_diagnostics(self) -> None:
    controller = FakeAutonomyController()
    with (
        patch.object(main, "get_autonomy_controller", return_value=controller),
        patch.object(main, "get_agent_graph", return_value=EmptyReplyGraph()),
        patch.dict(
            os.environ,
            {
                "BRAIN_AGENT_MODEL": "gpt-5.2",
                "OPENAI_BASE_URL": "https://example.invalid/v1",
            },
            clear=False,
        ),
        self.assertLogs(main.logger.name, level="WARNING") as logs,
    ):
        with TestClient(main.app) as client:
            create_response = client.post("/agent/sessions")
            session_id = create_response.json()["sessionId"]
            send_response = client.post(
                f"/agent/sessions/{session_id}/messages",
                json={"input": "hello"},
            )

    self.assertEqual(send_response.status_code, 200)
    self.assertEqual(
        send_response.json()["output"],
        "模型返回了空回复，请重试或更换模型配置。",
    )
    combined_logs = "\n".join(logs.output)
    self.assertIn("Agent returned empty final output", combined_logs)
    self.assertIn("gpt-5.2", combined_logs)
    self.assertIn("https://example.invalid/v1", combined_logs)
    self.assertIn("chatcmpl-empty-123", combined_logs)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest agent/tests/test_main_api.py -k empty_model_reply_diagnostics -v`
Expected: FAIL because no warning log is emitted yet

- [ ] **Step 3: Commit only if the test file is in the desired failing state and you are working incrementally**

```bash
git add agent/tests/test_main_api.py
git commit -m "test: add empty reply diagnostics regression"
```

If you prefer not to commit the red state separately, skip this step and continue directly to Task 2.

## Task 2: Add The Empty Reply Warning Log

**Files:**
- Modify: `agent/main.py`

- [ ] **Step 1: Add a module logger**

Near the imports and app setup, add:

```python
import logging

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Implement the minimal warning log in `_extract_final_output()`**

Update `_extract_final_output()` so that when `output` is empty it logs a warning before returning the fallback text:

```python
def _extract_final_output(messages: list[Any]) -> str:
    final_message = None
    final_index = None
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if getattr(message, "type", None) == "ai":
            final_message = message
            final_index = index
            break

    if final_message is None and messages:
        final_message = messages[-1]
        final_index = len(messages) - 1

    output = _normalize_message_content(
        getattr(final_message, "content", "No response from agent.")
    )
    if output:
        return output

    tail_types = [getattr(message, "type", type(message).__name__) for message in messages[-3:]]
    logger.warning(
        "Agent returned empty final output | model=%s base_url=%s message_type=%s final_index=%s message_count=%s content_repr=%r response_metadata=%r additional_kwargs=%r tail_message_types=%r",
        os.getenv("BRAIN_AGENT_MODEL", "gpt-4o-mini"),
        get_openai_base_url(),
        getattr(final_message, "type", type(final_message).__name__ if final_message is not None else None),
        final_index,
        len(messages),
        getattr(final_message, "content", None),
        getattr(final_message, "response_metadata", None),
        getattr(final_message, "additional_kwargs", None),
        tail_types,
    )
    return "模型返回了空回复，请重试或更换模型配置。"
```

This keeps the warning focused and avoids logging full transcript content.

- [ ] **Step 3: Run the focused test to verify it passes**

Run: `python -m pytest agent/tests/test_main_api.py -k empty_model_reply_diagnostics -v`
Expected: PASS

- [ ] **Step 4: Run the existing empty-reply fallback test too**

Run: `python -m pytest agent/tests/test_main_api.py -k empty_model_reply -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/main.py agent/tests/test_main_api.py
git commit -m "feat: log empty agent replies for diagnostics"
```

## Task 3: Verify Normal Replies Stay Quiet

**Files:**
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Add a focused non-empty reply test**

```python
def test_agent_sessions_do_not_log_empty_reply_warning_for_normal_output(self) -> None:
    controller = FakeAutonomyController()
    with (
        patch.object(main, "get_autonomy_controller", return_value=controller),
        patch.object(main, "get_agent_graph", return_value=FakeGraph()),
        self.assertLogs(main.logger.name, level="WARNING") as logs,
    ):
        with TestClient(main.app) as client:
            create_response = client.post("/agent/sessions")
            session_id = create_response.json()["sessionId"]
            send_response = client.post(
                f"/agent/sessions/{session_id}/messages",
                json={"input": "hello"},
            )

    self.assertEqual(send_response.status_code, 200)
    self.assertEqual(send_response.json()["output"], "interactive reply")
    combined_logs = "\n".join(logs.output)
    self.assertNotIn("Agent returned empty final output", combined_logs)
```
```

If `assertLogs` is too strict because no warnings are emitted, replace it with a custom logging handler or `unittest.mock.patch.object(main.logger, "warning")` and assert the mock was not called.

- [ ] **Step 2: Run the focused test to verify it fails correctly before adjustment**

Run: `python -m pytest agent/tests/test_main_api.py -k normal_output -v`
Expected: FAIL until the test uses the correct quiet-path assertion mechanism

- [ ] **Step 3: Use the minimal assertion mechanism that proves no warning was emitted**

Recommended implementation:

```python
def test_agent_sessions_do_not_log_empty_reply_warning_for_normal_output(self) -> None:
    controller = FakeAutonomyController()
    with (
        patch.object(main, "get_autonomy_controller", return_value=controller),
        patch.object(main, "get_agent_graph", return_value=FakeGraph()),
        patch.object(main.logger, "warning") as warning_log,
    ):
        with TestClient(main.app) as client:
            create_response = client.post("/agent/sessions")
            session_id = create_response.json()["sessionId"]
            send_response = client.post(
                f"/agent/sessions/{session_id}/messages",
                json={"input": "hello"},
            )

    self.assertEqual(send_response.status_code, 200)
    self.assertEqual(send_response.json()["output"], "interactive reply")
    warning_log.assert_not_called()
```

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `python -m pytest agent/tests/test_main_api.py -k normal_output -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/tests/test_main_api.py
git commit -m "test: verify empty reply diagnostics stay quiet on success"
```

## Task 4: Final Verification

**Files:**
- Modify: none expected unless verification reveals a real issue

- [ ] **Step 1: Run the main API test file**

Run: `python -m pytest agent/tests/test_main_api.py -v`
Expected: PASS

- [ ] **Step 2: Run the broader nearby suite**

Run: `python -m pytest agent/tests/test_autonomy.py agent/tests/test_autonomy_workflows.py agent/tests/test_main_api.py -q`
Expected: PASS

- [ ] **Step 3: Inspect git status for intended files only**

Run: `git status --short`
Expected: only `agent/main.py` and `agent/tests/test_main_api.py` are modified if no extra fixes were needed

- [ ] **Step 4: Commit verification-only fixes if needed**

```bash
git add agent/main.py agent/tests/test_main_api.py
git commit -m "test: verify empty reply diagnostics"
```

## Self-Review

### Spec Coverage

- warning log only on empty replies: Task 2
- include model and base URL context: Task 2
- include final content representation, response metadata, and additional kwargs: Task 2
- include message-count and tail-message summary: Task 2
- regression test proving warning is emitted: Task 1
- proof that normal replies do not emit the warning: Task 3

No spec requirement is left without an implementation step.

### Placeholder Scan

- no `TODO` or `TBD`
- exact files are named
- exact commands and expected outcomes are included

### Type Consistency

- the plan consistently uses `_extract_final_output()` as the instrumentation point
- the warning message string stays consistent: `Agent returned empty final output`
- the fallback output string remains `模型返回了空回复，请重试或更换模型配置。`
