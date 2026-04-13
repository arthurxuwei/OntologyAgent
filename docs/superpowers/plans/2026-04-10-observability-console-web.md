# Observability Console Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current chat-first web page with a console-first observability dashboard that shows runtime, freqtrade, and chain state clearly while preserving autonomy controls and chat on the same page.

**Architecture:** Keep the backend API surface mostly unchanged and drive the redesign from the existing `agent/web/chat.html` single-page app. Add small API tests to lock existing data contracts, then rebuild the page layout around a top control bar, three primary observability cards, a secondary detail band, and a demoted chat panel. Use front-end view-model helpers to map `/health` and `/autonomy/*` payloads into stable UI fragments instead of binding raw nested JSON directly in many places.

**Tech Stack:** Python 3, FastAPI, HTML, CSS, vanilla JavaScript, `unittest`, `fastapi.testclient`

---

## File Map

- Modify: `agent/tests/test_main_api.py`
  - Add API coverage for the console fields the new page depends on
- Modify: `agent/web/chat.html`
  - Replace the chat-first layout with a console-first dashboard layout
  - Add CSS for top bar, observability cards, detail strip, and mobile stacking
  - Replace the current DOM update code with front-end view-model helpers and dashboard-specific render functions
- Modify: `agent/main.py`
  - No major backend changes expected, but this file may need a small fix only if a required field proves inconsistent while writing tests

## Task 1: Lock The Existing API Data Contract For The Console

**Files:**
- Modify: `agent/tests/test_main_api.py`
- Modify: `agent/main.py` only if a test reveals a real gap

- [ ] **Step 1: Write the failing API tests for console data fields**

```python
def test_health_includes_runtime_freqtrade_and_chain_console_fields(self) -> None:
    controller = FakeAutonomyController()

    class FakeChainClient:
        async def list_tools(self) -> list[str]:
            return ["chain_sign_transfer", "chain_get_wallet_state"]

    class FakeFreqtradeClient:
        async def list_tools(self) -> list[str]:
            return ["get_trading_status"]

    with (
        patch.object(main, "get_autonomy_controller", return_value=controller),
        patch.object(main, "get_chain_mcp_client", return_value=FakeChainClient()),
        patch.object(main, "get_freqtrade_mcp_client", return_value=FakeFreqtradeClient()),
        patch.object(main, "get_chain_wallet_state", return_value={"wallet": {"address": "0xabc", "balanceEth": "1.0"}}),
        patch.object(main, "get_freqtrade_status_snapshot", return_value={"state": "running", "runmode": "dry_run", "exchange": "binance", "strategy": "SimpleAgentStrategy", "openTradeCount": 2}),
    ):
        with TestClient(main.app) as client:
            response = client.get("/health")

    payload = response.json()
    self.assertIn("autonomy", payload)
    self.assertIn("freqtradeStatus", payload)
    self.assertIn("chainWallet", payload)
    self.assertIn("recentChainAction", payload)


def test_autonomy_start_and_stop_keep_summary_shape(self) -> None:
    controller = FakeAutonomyController()

    with patch.object(main, "get_autonomy_controller", return_value=controller):
        with TestClient(main.app) as client:
            start_response = client.post("/autonomy/start")
            stop_response = client.post("/autonomy/stop")

    self.assertIn("summary", start_response.json())
    self.assertIn("summary", stop_response.json())
```

- [ ] **Step 2: Run the tests to verify they fail if any required field is missing**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_main_api.py -k "console_fields or summary_shape" -v`
Expected: PASS if the current API already satisfies the console data contract, otherwise FAIL on the missing field

- [ ] **Step 3: Make the minimal backend fix only if a test revealed a real field gap**

```python
# agent/main.py
@app.post("/autonomy/start")
async def autonomy_start() -> dict[str, Any]:
    controller = get_autonomy_controller()
    await controller.start(force=True)
    return _with_autonomy_runtime_summary(await controller.status())
```

Use the same pattern for `/autonomy/stop` or any equally small fix only if the failing test proves it is needed.

- [ ] **Step 4: Run the focused API tests to verify they pass**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_main_api.py -k "console_fields or summary_shape" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/tests/test_main_api.py agent/main.py
git commit -m "test: lock observability console API contract"
```

## Task 2: Replace The Page Skeleton With A Console-First Layout

**Files:**
- Modify: `agent/web/chat.html`

- [ ] **Step 1: Write the failing page-structure test**

```python
def test_chat_page_contains_console_first_sections(self) -> None:
    controller = FakeAutonomyController()
    with patch.object(main, "get_autonomy_controller", return_value=controller):
        with TestClient(main.app) as client:
            response = client.get("/")

    self.assertIn("Runtime", response.text)
    self.assertIn("Freqtrade", response.text)
    self.assertIn("Chain", response.text)
    self.assertIn("Recent Chain Action", response.text)
    self.assertIn("管家", response.text)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_main_api.py -k "console_first_sections" -v`
Expected: FAIL because the current page still uses the old chat-first labels and structure

- [ ] **Step 3: Replace the main HTML structure with the new console skeleton**

```html
<section class="hero">
  <span class="eyebrow">OntologyAgent Console</span>
  <h1>自治操作台</h1>
</section>

<div class="card console-shell">
  <section class="topbar">
    <div class="topbar-summary">
      <h2>Runtime Control</h2>
      <p id="last-refresh">Last refresh: never</p>
    </div>
    <div class="topbar-actions">
      <div class="status-pill" id="connection-status">Ready</div>
      <button class="primary" id="start-button">Start</button>
      <button class="secondary" id="tick-button">Tick</button>
      <button class="ghost" id="stop-button">Stop</button>
    </div>
  </section>

  <section class="observability-grid">
    <article class="panel-card" id="runtime-card">
      <h3>Runtime</h3>
    </article>
    <article class="panel-card" id="freqtrade-card">
      <h3>Freqtrade</h3>
    </article>
    <article class="panel-card" id="chain-card">
      <h3>Chain</h3>
    </article>
  </section>

  <section class="detail-grid">
    <article class="panel-card">
      <h3>Recent Chain Action</h3>
    </article>
    <article class="panel-card">
      <h3>Execution Snapshot</h3>
    </article>
    <article class="panel-card">
      <h3>Warnings / Errors</h3>
    </article>
  </section>

  <section class="chat-panel">
    <h3>管家</h3>
  </section>
</div>
```

- [ ] **Step 4: Run the page-structure test to verify it passes**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_main_api.py -k "console_first_sections" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/web/chat.html agent/tests/test_main_api.py
git commit -m "feat: rebuild web page as observability console"
```

## Task 3: Add Console Styling And Responsive Layout

**Files:**
- Modify: `agent/web/chat.html`

- [ ] **Step 1: Write the failing style markers test**

```python
def test_chat_page_includes_console_layout_classes(self) -> None:
    controller = FakeAutonomyController()
    with patch.object(main, "get_autonomy_controller", return_value=controller):
        with TestClient(main.app) as client:
            response = client.get("/")

    self.assertIn("console-shell", response.text)
    self.assertIn("observability-grid", response.text)
    self.assertIn("detail-grid", response.text)
    self.assertIn("@media (max-width: 960px)", response.text)
```

- [ ] **Step 2: Run the test to verify it fails if the class names or media rules are not in place**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_main_api.py -k "console_layout_classes" -v`
Expected: FAIL until the CSS and layout classes exist

- [ ] **Step 3: Add the console-first CSS and mobile stacking rules**

```css
.console-shell {
  display: grid;
  gap: 20px;
  padding: 24px;
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.observability-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.panel-card {
  padding: 18px;
  border-radius: 18px;
  border: 1px solid rgba(215, 201, 170, 0.85);
  background: rgba(255, 249, 239, 0.85);
}

.chat-panel {
  border-top: 1px solid rgba(215, 201, 170, 0.7);
  padding-top: 12px;
}

@media (max-width: 960px) {
  .topbar {
    flex-direction: column;
    align-items: stretch;
  }

  .observability-grid,
  .detail-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 4: Run the style markers test to verify it passes**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_main_api.py -k "console_layout_classes" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/web/chat.html agent/tests/test_main_api.py
git commit -m "feat: add responsive observability console layout"
```

## Task 4: Add Front-End View Models For Runtime, Freqtrade, And Chain

**Files:**
- Modify: `agent/web/chat.html`

- [ ] **Step 1: Write the failing view-model marker test**

```python
def test_chat_page_declares_console_view_model_helpers(self) -> None:
    controller = FakeAutonomyController()
    with patch.object(main, "get_autonomy_controller", return_value=controller):
        with TestClient(main.app) as client:
            response = client.get("/")

    self.assertIn("function buildRuntimeViewModel", response.text)
    self.assertIn("function buildFreqtradeViewModel", response.text)
    self.assertIn("function buildChainViewModel", response.text)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_main_api.py -k "view_model_helpers" -v`
Expected: FAIL because these helpers do not exist yet

- [ ] **Step 3: Add the front-end view-model helpers**

```javascript
function buildRuntimeViewModel(payload) {
  const autonomy = payload?.autonomy ?? {};
  const ledger = autonomy?.ledger ?? {};
  return {
    enabled: Boolean(autonomy?.enabled),
    running: Boolean(autonomy?.running),
    circuitState: autonomy?.summary?.circuitState ?? ledger?.circuitBreaker?.state ?? "closed",
    activeExecutionCount: autonomy?.summary?.activeExecutionCount ?? 0,
    executionHistoryCount: Array.isArray(ledger?.executionHistory) ? ledger.executionHistory.length : 0,
    lastTickAt: ledger?.lastTickAt ?? "unknown",
  };
}

function buildFreqtradeViewModel(payload) {
  const status = payload?.freqtradeStatus ?? {};
  return {
    state: status?.runningState ?? status?.state ?? "unknown",
    runmode: status?.runmode ?? "unknown",
    exchange: status?.exchange ?? "unknown",
    strategy: status?.strategy ?? "unknown",
    openTradeCount: typeof status?.openTradeCount === "number" ? status.openTradeCount : null,
    error: status?.error ?? null,
  };
}

function buildChainViewModel(payload) {
  const wallet = payload?.chainWallet?.wallet ?? {};
  const action = payload?.recentChainAction?.summary ?? {};
  return {
    address: wallet?.address ?? "未配置",
    balanceEth: wallet?.balanceEth ?? wallet?.balance ?? "未知",
    actionKind: action?.kind ?? "暂无",
    actionStatus: action?.status ?? "unknown",
    identifier: action?.txHash ?? action?.userOpHash ?? "-",
    actionTarget: action?.to ?? action?.target ?? null,
    actionAt: payload?.recentChainAction?.at ?? null,
  };
}
```

- [ ] **Step 4: Run the view-model marker test to verify it passes**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_main_api.py -k "view_model_helpers" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/web/chat.html agent/tests/test_main_api.py
git commit -m "feat: add observability console view models"
```

## Task 5: Render Dashboard Cards And Detail Panels

**Files:**
- Modify: `agent/web/chat.html`

- [ ] **Step 1: Write the failing render marker test**

```python
def test_chat_page_renders_runtime_freqtrade_chain_detail_targets(self) -> None:
    controller = FakeAutonomyController()
    with patch.object(main, "get_autonomy_controller", return_value=controller):
        with TestClient(main.app) as client:
            response = client.get("/")

    self.assertIn('id="runtime-status"', response.text)
    self.assertIn('id="freqtrade-state"', response.text)
    self.assertIn('id="chain-identifier"', response.text)
    self.assertIn('id="execution-snapshot"', response.text)
    self.assertIn('id="warnings-panel"', response.text)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_main_api.py -k "renders_runtime_freqtrade_chain_detail_targets" -v`
Expected: FAIL because the new dashboard field targets do not exist yet

- [ ] **Step 3: Add the dashboard metric DOM targets and render helpers**

```html
<article class="panel-card" id="runtime-card">
  <h3>Runtime</h3>
  <div class="metric" id="runtime-status">unknown</div>
  <div class="meta-row"><span class="meta-label">Circuit</span><span class="meta-value" id="runtime-circuit">unknown</span></div>
  <div class="meta-row"><span class="meta-label">Active Executions</span><span class="meta-value" id="runtime-active-count">0</span></div>
</article>
```

```javascript
function renderRuntimeCard(viewModel) {
  document.getElementById("runtime-status").textContent = viewModel.running ? "running" : viewModel.enabled ? "enabled" : "stopped";
  document.getElementById("runtime-circuit").textContent = viewModel.circuitState;
  document.getElementById("runtime-active-count").textContent = String(viewModel.activeExecutionCount);
}

function renderFreqtradeCard(viewModel) {
  document.getElementById("freqtrade-state").textContent = viewModel.state;
  document.getElementById("freqtrade-open-trades").textContent = viewModel.openTradeCount == null ? "-" : String(viewModel.openTradeCount);
}

function renderChainCard(viewModel) {
  document.getElementById("chain-address").textContent = viewModel.address;
  document.getElementById("chain-identifier").textContent = viewModel.identifier;
}
```

- [ ] **Step 4: Run the render marker test to verify it passes**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_main_api.py -k "renders_runtime_freqtrade_chain_detail_targets" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/web/chat.html agent/tests/test_main_api.py
git commit -m "feat: render observability console cards"
```

## Task 6: Wire Actions And Health Refresh Into The New Console

**Files:**
- Modify: `agent/web/chat.html`

- [ ] **Step 1: Write the failing action wiring marker test**

```python
def test_chat_page_uses_console_action_buttons_and_refresh_helpers(self) -> None:
    controller = FakeAutonomyController()
    with patch.object(main, "get_autonomy_controller", return_value=controller):
        with TestClient(main.app) as client:
            response = client.get("/")

    self.assertIn('id="start-button"', response.text)
    self.assertIn('id="tick-button"', response.text)
    self.assertIn('id="stop-button"', response.text)
    self.assertIn("async function refreshDashboard", response.text)
```

- [ ] **Step 2: Run the test to verify it fails if the new button wiring or refresh helper is missing**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_main_api.py -k "console_action_buttons_and_refresh_helpers" -v`
Expected: FAIL until the new action wiring is present

- [ ] **Step 3: Replace the old guard action select flow with direct console action wiring**

```javascript
async function refreshDashboard() {
  const payload = await requestJson("/health", { method: "GET" });
  renderRuntimeCard(buildRuntimeViewModel(payload));
  renderFreqtradeCard(buildFreqtradeViewModel(payload));
  renderChainCard(buildChainViewModel(payload));
  renderDetailPanels(payload);
}

startButtonEl.addEventListener("click", async () => {
  setBusy(true, "Starting");
  try {
    const payload = await requestJson("/autonomy/start", { method: "POST" });
    updateGuardResult("启动理财子", payload);
    await refreshDashboard();
  } finally {
    setBusy(false);
  }
});
```

Use the same pattern for `tick` and `stop`.

- [ ] **Step 4: Run the action wiring marker test to verify it passes**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_main_api.py -k "console_action_buttons_and_refresh_helpers" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/web/chat.html agent/tests/test_main_api.py
git commit -m "feat: wire console actions and dashboard refresh"
```

## Task 7: Final Verification

**Files:**
- Modify: none expected unless verification reveals a real issue
- Test: `agent/tests/test_main_api.py`

- [ ] **Step 1: Run the main API test file**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_main_api.py -v`
Expected: PASS

- [ ] **Step 2: Run the broader agent test suite to ensure the page changes did not regress nearby behavior**

Run: `PYTHONPATH=agent /Users/freedom/cc/OntologyAgent/.worktrees/agent-autonomy-p0/.venv/bin/python -m pytest agent/tests/test_autonomy.py agent/tests/test_autonomy_workflows.py agent/tests/test_main_api.py -q`
Expected: PASS

- [ ] **Step 3: Inspect git status for intended files only**

Run: `git status --short`
Expected: only these files changed if no extra fixes were needed:
- `agent/web/chat.html`
- `agent/tests/test_main_api.py`
- `agent/main.py` only if Task 1 revealed a real API contract gap

- [ ] **Step 4: Commit verification-only fixes if needed**

```bash
git add agent/web/chat.html agent/tests/test_main_api.py agent/main.py
git commit -m "test: verify observability console web"
```

## Self-Review

### Spec Coverage

- console-first layout: Tasks 2 and 3
- runtime, freqtrade, and chain cards: Tasks 2, 4, and 5
- recent chain action and detail panels: Task 5
- action-first top bar and refresh behavior: Task 6
- mobile stacking and responsive behavior: Task 3
- reuse of existing backend endpoints: Task 1 and Task 6

No spec section is left without a matching implementation task.

### Placeholder Scan

- no `TODO` or `TBD`
- all tasks name exact files
- all code steps include concrete code blocks
- all verification steps include exact commands and expected outcomes

### Type Consistency

- the plan consistently uses `buildRuntimeViewModel`, `buildFreqtradeViewModel`, and `buildChainViewModel`
- the plan keeps the page as a single `agent/web/chat.html` console-first implementation rather than introducing a second page
