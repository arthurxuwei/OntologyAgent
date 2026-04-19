## Overview

The autonomy subsystem persists its last error in `agent/data/autonomy_state.json` and surfaces that state through `/health`, but the current payload only includes the error message string. When the environment changes later, operators can still see the old error and have no reliable way to tell when it actually occurred.

This feature adds an explicit timestamp for the most recent autonomy error and exposes it all the way through the existing status and warnings UI.

## Problem Statement

Today:

- autonomy persists `lastError`
- `/health` returns the ledger state
- the web UI shows `Autonomy error: <message>`

What is missing:

- the time the error was recorded

Without that timestamp, an operator cannot distinguish between:

- a fresh runtime failure that just happened
- a stale persisted error from a previous startup or misconfiguration

This is especially confusing when the underlying issue has already been corrected, but the persisted state still carries the previous error string.

## Goals

- Persist the timestamp of the latest autonomy error
- Expose the timestamp through the existing `/health` payload
- Show the timestamp directly in the `Status` / `Warnings` UI
- Clear the timestamp when a later successful tick clears the error
- Keep backward compatibility with older persisted state files that do not have the new field

## Non-Goals

- Storing an error history list
- Building a full error timeline UI
- Relative-time or locale-specific timestamp formatting
- Changing unrelated warning types

## Considered Approaches

### Approach 1: Recommended

Add a single `lastErrorAt` field alongside `lastError` and display it when present.

Pros:

- Smallest change that solves the ambiguity
- Backward-compatible with existing state files
- Matches the project’s existing `...At` field naming style

Cons:

- Only preserves the most recent error, not full history

### Approach 2: Error History List

Persist multiple error events with timestamps and render the latest one in the UI.

Pros:

- Better forensic value

Cons:

- More schema and UI complexity than needed for the immediate problem

### Approach 3: UI-only Local Timestamp

Add a timestamp only when the frontend first observes an error.

Pros:

- Small frontend-only change

Cons:

- Not the actual occurrence time
- Lost on refresh or service restart
- Does not solve ambiguity in persisted state

## Chosen Design

Add `lastErrorAt` to the autonomy ledger state, set it whenever a new autonomy error is recorded, clear it whenever a successful tick clears `lastError`, and display it in the warnings UI when available.

## Architecture

### 1. Ledger Schema

Add a new field to the persisted autonomy ledger:

- `lastErrorAt: Optional[str] = None`

The timestamp format should stay consistent with the existing project convention and use `utcnow_iso()`.

### 2. Error Recording Semantics

Whenever the background autonomy loop catches an exception and records `lastError`, it must also record:

- `lastErrorAt = utcnow_iso()`

This should happen in the same error-handling path where `lastError` is written and `_save_state()` is called.

### 3. Error Clearing Semantics

Whenever a later successful tick clears `lastError`, the code must also clear:

- `lastErrorAt = None`

This should happen in both successful state refresh/update paths so the stored timestamp never looks active when the error has already been cleared.

### 4. API Exposure

No new endpoint is needed.

The existing `/health` payload already exposes `autonomy.ledger`. The new `lastErrorAt` field should simply flow through that payload alongside `lastError`.

### 5. UI Rendering

The warnings panel should render autonomy errors using these rules:

- if `lastError` is absent: show no autonomy error warning
- if `lastError` exists and `lastErrorAt` is missing: show the legacy form
  - `Autonomy error: <message>`
- if `lastError` and `lastErrorAt` both exist: show
  - `Autonomy error at <timestamp>: <message>`

This preserves compatibility with older state files and mixed-version environments.

## V1 Scope

Included in V1:

- adding `lastErrorAt` to the autonomy ledger
- writing it on error
- clearing it on success
- exposing it via `/health`
- rendering it in the warnings UI

Explicitly excluded from V1:

- multiple stored errors
- separate warnings history page
- localization or humanized times such as “2 minutes ago”

## Failure Handling

### Older State Files

Older `autonomy_state.json` files will not contain `lastErrorAt`. The new field must therefore remain optional and default to `None`.

### Mixed Runtime States

If `lastError` exists but `lastErrorAt` does not, the UI must continue to render the message instead of failing or hiding the warning.

### Clearing Behavior

If a successful tick clears the error but leaves the timestamp behind, the UI would still look stale. Therefore, the implementation must clear both fields together.

## Testing Strategy

The feature should be verified at two levels.

### Backend Tests

Add tests proving:

- an autonomy failure records both `lastError` and `lastErrorAt`
- a later successful update clears both `lastError` and `lastErrorAt`

### UI / View-Model Tests

Add tests proving:

- with only `lastError`, the warnings model returns `Autonomy error: ...`
- with both `lastError` and `lastErrorAt`, the warnings model returns `Autonomy error at <timestamp>: ...`

## Summary

The correct fix is to add `lastErrorAt` to the persisted autonomy state, set it when errors occur, clear it when the error is cleared, and show it directly in the warnings UI. This is the smallest change that lets operators distinguish fresh autonomy failures from old persisted ones.
