# Observability Console Web Design

## Summary

This spec defines a redesign of the current web page into a console-first observability surface.

The repository already exposes useful runtime, trading, and chain status through `/health` and `/autonomy/*`, but the current page is still chat-first and treats observability as a side panel. The goal of this work is to make the web page function as an operational console first and a chat assistant second.

The first version should reuse the existing backend endpoints and reorganize the front-end presentation into a clean, scan-friendly dashboard that supports both monitoring and direct control actions.

## Current Context

The current page in `agent/web/chat.html` already includes:

- autonomy controls
- chain status snippets
- recent chain action summary
- freqtrade status snippets
- embedded chat interaction

The current backend already exposes enough data for a useful first pass:

- `/health`
- `/autonomy/status`
- `/autonomy/start`
- `/autonomy/stop`
- `/autonomy/tick`

The problem is not missing basic observability data. The problem is that the page layout is still organized around chat, so the operational picture is fragmented and harder to scan quickly.

## Scope

### Included

- replace the current chat-first page structure with a console-first layout
- display runtime, freqtrade, and chain observability in distinct dashboard sections
- keep autonomy control actions visible and immediately usable
- keep chat available as a secondary tool on the same page
- implement responsive behavior for desktop and mobile
- reuse existing backend endpoints for the first version

### Excluded

- websocket or push-based live updates
- historical charts or time-series graphs
- a multi-page admin application
- raw JSON inspector panels
- new complex backend endpoints unless a truly required field is missing

## Design Goals

- make the page usable as an operator console at a glance
- surface the most important risk and runtime information without requiring JSON reading
- preserve direct actions for start, stop, and tick
- keep the design visually coherent with the current single-page console style while allowing a full layout reset
- ensure mobile readability without collapsing the dashboard into unusable fragments

## Recommended Approach

Three approaches were considered:

1. console-first dashboard redesign
2. continue extending the current chat-first page
3. split monitoring and chat into separate pages

The recommended approach is the console-first dashboard redesign.

It gives the page a clear operating purpose:

- status and controls first
- chat second

It also avoids the cost of a multi-page split while preventing the current patchwork layout from becoming harder to use over time.

## Information Architecture

The page should be organized into four primary layers.

### 1. Top Status Bar

The top bar should provide immediate situational awareness and primary actions.

Contents:

- page title and product identity
- last refresh time
- autonomy runtime status badge
- start button
- stop button
- tick button

This bar should remain concise and avoid detailed field lists.

### 2. Main Observability Grid

The main dashboard area should present three primary cards side by side on desktop and stacked on mobile.

Cards:

- `Runtime`
- `Freqtrade`
- `Chain`

These are the core operational surfaces and should appear before any chat content.

### 3. Secondary Detail Strip

Below the main observability grid, the page should provide a smaller detail band for short summaries that matter operationally but do not deserve top-card priority.

Suggested panels:

- `Recent Chain Action`
- `Execution Snapshot`
- `Warnings / Errors`

This area should be summary-oriented, not log-oriented.

### 4. Agent Panel

The chat and session UI should remain on the page, but it should become a secondary panel below the console content rather than the dominant frame of the layout.

## Runtime Card Design

The `Runtime` card should expose the minimum set of fields needed to understand whether autonomy is operating safely.

Primary fields:

- `enabled`
- `running`
- `circuitState`
- `activeExecutionCount`
- `executionHistory` count
- `lastTickAt`

Visual rules:

- open circuit breaker uses danger styling
- enabled but not running should be visually highlighted as abnormal
- active execution count uses informational emphasis rather than danger styling

This card should make it obvious whether the runtime is healthy, paused, blocked, or actively doing work.

## Freqtrade Card Design

The `Freqtrade` card should summarize the current trading-side state without turning into a raw status dump.

Primary fields:

- `runningState` or `state`
- `runmode`
- `exchange`
- `strategy`
- `openTradeCount`

Visual rules:

- non-running state is highlighted
- open trade count is visible but not treated as an error by itself
- if an error field exists, show an explicit warning badge

This card should answer: is freqtrade running, in what mode, under which strategy, and with how many open trades.

## Chain Card Design

The `Chain` card should summarize the current wallet and settlement-side operating context.

Primary fields:

- wallet address
- wallet balance
- most recent chain action kind
- most recent chain action status
- most recent `txHash` or `userOpHash`
- recent action timestamp

Visual rules:

- `failed` or `reverted` statuses use danger styling
- `pending` or `submitted` statuses use informational styling
- terminal success uses success styling

This card should answer: what happened most recently on chain, and is it pending, successful, or problematic.

## Secondary Detail Panels

### Recent Chain Action

This panel should display a compact human-readable summary derived from the existing `recentChainAction` payload.

Examples:

- submitted execution to target `0xabc`
- submitted user operation for target `0xdef`
- receipt query showing `pending`
- user operation status query showing `failed`

### Execution Snapshot

This panel should summarize current runtime execution state using existing autonomy data.

Examples:

- number of active executions
- latest execution stage
- latest known circuit state

### Warnings / Errors

This panel should summarize visible backend-level issues already present in `/health`.

Examples:

- chain MCP unavailable
- freqtrade MCP unavailable
- autonomy controller error

If no warnings exist, show a quiet healthy state instead of an empty panel.

## View-Model Strategy

The page should not bind directly to raw backend payloads everywhere.

Recommended front-end structure:

- `buildRuntimeViewModel(payload)`
- `buildFreqtradeViewModel(payload)`
- `buildChainViewModel(payload)`

These helpers should map nested backend responses into small, stable UI objects.

Benefits:

- reduces template complexity
- makes future backend field changes easier to absorb
- keeps display logic in one place instead of spread through DOM updates

## Visual State System

The page should standardize around four display states:

- `neutral`
- `info`
- `success`
- `danger`

These states should consistently drive:

- status badges
- metric emphasis
- border or outline accents
- warning text styles

The goal is to make scanning behavior consistent across cards.

## Action Flow

The control actions should remain first-class.

Required actions:

- start autonomy
- stop autonomy
- tick autonomy

Expected behavior:

- after action completion, the page refreshes observability data
- the refreshed cards reflect the new runtime state
- action feedback remains visible and does not require the user to inspect logs

## Responsive Behavior

### Desktop

Desktop should use a multi-column dashboard:

- top bar across full width
- three-card observability grid
- secondary detail strip
- chat panel below

### Mobile

Mobile should stack content in this order:

1. top status bar
2. runtime card
3. freqtrade card
4. chain card
5. secondary detail panels
6. chat panel

No critical card should rely on side-by-side reading on small screens.

## Implementation Strategy

The first version should avoid backend churn.

Preferred implementation order:

1. replace the page structure in `agent/web/chat.html`
2. introduce front-end view-model helpers for runtime, freqtrade, and chain cards
3. wire controls to the existing autonomy endpoints
4. refresh cards after actions and periodic page refresh
5. add or update front-end tests if the current project pattern supports them
6. add backend fields only if the new layout reveals a true data gap

This keeps the scope focused on turning existing observability data into a better operator experience.

## Acceptance Criteria

This redesign is complete when all of the following are true:

1. the web page is clearly console-first rather than chat-first
2. runtime, freqtrade, and chain each have a dedicated visible card
3. autonomy start, stop, and tick actions remain available and update the page state after completion
4. recent chain activity is shown in a readable summarized form
5. runtime risk signals such as circuit breaker state are visually prominent
6. the page is readable on both desktop and mobile
7. the first version works using existing backend endpoints

## Final Recommendation

Do not keep extending the current page as a chat layout with more side widgets.

Treat this as a console redesign.

The backend already has enough observability data for a strong first pass. The highest-value change now is to reorganize the page into a dashboard that makes runtime, trading, and chain state readable at a glance while preserving chat as a supporting tool.
