# Autonomy Error Timestamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `lastErrorAt` to persisted autonomy state and show that timestamp directly in the Status/Warnings UI when an autonomy error exists.

**Architecture:** Extend the `GuardLedger` schema with a nullable `lastErrorAt`, write it whenever the autonomy loop records a failure, clear it whenever a later successful tick clears `lastError`, and update the warnings view-model in `agent/web/chat.html` to render `Autonomy error at <timestamp>: ...` when both fields are present. Keep backward compatibility by preserving the old warning format when older state files only contain `lastError`.

**Tech Stack:** Python (`pydantic`, `unittest`, FastAPI), browser-side JavaScript in `agent/web/chat.html`

---

## File Map

- Modify: `agent/autonomy.py`
  Responsibility: add `lastErrorAt` to the persisted ledger model, set it on failure, and clear it on successful state refresh/update.
- Modify: `agent/tests/test_autonomy.py`
  Responsibility: verify error recording writes `lastErrorAt` and successful ticks clear it.
- Modify: `agent/web/chat.html`
  Responsibility: render autonomy warnings with `lastErrorAt` when present while preserving the legacy fallback format.
- Modify: `agent/tests/test_main_api.py`
  Responsibility: verify the warnings view-model renders both the timestamped and legacy autonomy warning formats.

## Implementation Notes

- New ledger field: `lastErrorAt: Optional[str] = None`
- Timestamp source: `utcnow_iso()`
- On loop failure:
  - set `self._state.lastError = str(error)`
  - set `self._state.lastErrorAt = utcnow_iso()`
- On later successful state refresh/update:
  - set `self._state.lastError = None`
  - set `self._state.lastErrorAt = None`
- UI rendering rules:
  - if `ledger.lastError` exists and `ledger.lastErrorAt` exists:
    - `Autonomy error at <timestamp>: <message>`
  - if `ledger.lastError` exists without `ledger.lastErrorAt`:
    - `Autonomy error: <message>`
  - if neither exists:
    - no autonomy warning

### Task 1: Add `lastErrorAt` to the Autonomy Ledger and Failure Paths

**Files:**
- Modify: `agent/autonomy.py`
- Modify: `agent/tests/test_autonomy.py`

- [ ] **Step 1: Add a failing backend test for error timestamp persistence**

Append to `agent/tests/test_autonomy.py`:

```python
    def test_run_loop_records_last_error_timestamp_on_failure(self) -> None:
        async def chain_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
            return make_chain_state("0")

        async def freqtrade_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
            return make_freqtrade_budget()

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            async def fail_once() -> None:
                raise RuntimeError("Autonomy requires a configured chain signer")

            with patch.object(controller, "tick", side_effect=fail_once):
                async def run_once() -> None:
                    controller._stop_event = asyncio.Event()
                    controller._stop_event.set()
                    await controller._run_loop()

                asyncio.run(run_once())

            ledger = asyncio.run(controller.status())["ledger"]
            self.assertEqual(ledger["lastError"], "Autonomy requires a configured chain signer")
            self.assertIsInstance(ledger["lastErrorAt"], str)
            self.assertTrue(ledger["lastErrorAt"])
```

- [ ] **Step 2: Run the focused backend test to verify it fails**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_autonomy.AutonomyControllerTests.test_run_loop_records_last_error_timestamp_on_failure`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: FAIL because `lastErrorAt` is not yet present.

- [ ] **Step 3: Add `lastErrorAt` to `GuardLedger` and write/clear it in `agent/autonomy.py`**

Add the new field to `GuardLedger`:

```python
    lastError: Optional[str] = None
    lastErrorAt: Optional[str] = None
    tickCount: int = 0
```

Update the loop failure path:

```python
                except Exception as error:
                    self._state.lastError = str(error)
                    self._state.lastErrorAt = utcnow_iso()
                    self._save_state()
```

Update both successful clearing paths:

```python
        self._state.lastTickAt = utcnow_iso()
        self._state.lastError = None
        self._state.lastErrorAt = None
        self._state.tickCount += 1
```

Apply that same clearing logic in both `_refresh_state_from_context()` and `_update_state()`.

- [ ] **Step 4: Run the focused backend test again**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_autonomy.AutonomyControllerTests.test_run_loop_records_last_error_timestamp_on_failure`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 5: Add a failing backend test for clearing `lastErrorAt` on success**

Append to `agent/tests/test_autonomy.py`:

```python
    def test_tick_clears_last_error_timestamp_after_success(self) -> None:
        async def chain_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
            return make_chain_state("1.0")

        async def freqtrade_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
            return make_freqtrade_budget(open_trades=0)

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )
            controller._state.lastError = "old failure"
            controller._state.lastErrorAt = "2026-04-19T10:00:00Z"

            asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]
            self.assertIsNone(ledger["lastError"])
            self.assertIsNone(ledger["lastErrorAt"])
```

- [ ] **Step 6: Run the focused backend clearing test**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_autonomy.AutonomyControllerTests.test_tick_clears_last_error_timestamp_after_success`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agent/autonomy.py agent/tests/test_autonomy.py
git commit -m "feat: persist autonomy error timestamps"
```

### Task 2: Render Timestamped Autonomy Warnings in the UI

**Files:**
- Modify: `agent/web/chat.html`
- Modify: `agent/tests/test_main_api.py`

- [ ] **Step 1: Add a failing UI/view-model test in `agent/tests/test_main_api.py`**

In the existing warning payload fixture under `test_home_page_script_helpers_build_runtime_and_warning_view_models`, change the health payload autonomy ledger to include:

```python
                        "lastError": "tick failed",
                        "lastErrorAt": "2026-04-19T10:05:23Z",
```

Then update the expected warnings list entry from:

```python
                "Autonomy error: tick failed",
```

to:

```python
                "Autonomy error at 2026-04-19T10:05:23Z: tick failed",
```

Also extend `payloads["healthWrapped"]` to include a ledger with:

```python
                        "lastError": "legacy failure",
```

and assert the legacy fallback still appears in `warningsWrapped`:

```python
        self.assertEqual(helper_output["warningsWrapped"], ["Autonomy error: legacy failure"])
```

- [ ] **Step 2: Run the focused UI/view-model test to verify it fails**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_main_api.MainApiTests.test_home_page_script_helpers_build_runtime_and_warning_view_models`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: FAIL because the JS warning builder does not yet include timestamps.

- [ ] **Step 3: Update `buildWarningsViewModel()` in `agent/web/chat.html`**

Replace the autonomy ledger warning block:

```javascript
        if (ledger.lastError) {
          pushWarning(`Autonomy error: ${ledger.lastError}`);
        }
```

with:

```javascript
        if (ledger.lastError && ledger.lastErrorAt) {
          pushWarning(`Autonomy error at ${ledger.lastErrorAt}: ${ledger.lastError}`);
        } else if (ledger.lastError) {
          pushWarning(`Autonomy error: ${ledger.lastError}`);
        }
```

- [ ] **Step 4: Run the focused UI/view-model test again**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_main_api.MainApiTests.test_home_page_script_helpers_build_runtime_and_warning_view_models`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/web/chat.html agent/tests/test_main_api.py
git commit -m "feat: show autonomy error timestamps in warnings"
```

### Task 3: End-to-End Verification

**Files:**
- Verify only: autonomy state, warnings UI, health payload

- [ ] **Step 1: Run all affected agent tests**

Run: `PYTHONPATH=agent python3 -m unittest agent.tests.test_autonomy agent.tests.test_main_api`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: PASS.

- [ ] **Step 2: Rebuild and restart the agent service**

Run: `docker compose --env-file "/Users/freedom/cc/OntologyAgent/.env" up -d --build agent`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: `agent` restarts successfully.

- [ ] **Step 3: Verify `/health` now exposes `lastErrorAt` when an autonomy error exists**

Run: `curl -fsS "http://localhost:8000/health"`
Workdir: `/Users/freedom/cc/OntologyAgent`
Expected: JSON response where `autonomy.ledger.lastErrorAt` is present when an autonomy error is persisted, or `null`/absent when cleared.

- [ ] **Step 4: Commit the final verified state**

```bash
git add agent/autonomy.py agent/tests/test_autonomy.py agent/web/chat.html agent/tests/test_main_api.py
git commit -m "feat: timestamp persisted autonomy errors"
```

## Self-Review

- Spec coverage:
  - `lastErrorAt` persistence: Task 1
  - write on failure / clear on success: Task 1
  - timestamped warnings UI with legacy fallback: Task 2
  - end-to-end `/health` verification: Task 3
- Placeholder scan: no `TODO`, `TBD`, or vague steps remain.
- Type consistency:
  - `lastErrorAt` is consistently optional and string-typed
  - warnings render `Autonomy error at <timestamp>: <message>` only when both values exist
