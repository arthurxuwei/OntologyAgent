# Agent Streaming Chat Revised Design

## Summary

This spec revises the earlier streaming chat design to simplify the contract:

- keep only the streaming session-chat path
- remove the old synchronous `POST /agent/sessions/{session_id}/messages` endpoint

The previous design preserved the synchronous endpoint for compatibility, but that leaves the codebase maintaining two different write paths for the same session history. Given the current architecture, that dual-path model increases complexity, keeps more regression surface alive, and makes session consistency harder to reason about.

This revised design treats streaming as the single supported session-chat protocol.

## Current Context

The repository currently has:

- `POST /agent/sessions`
- `GET /agent/sessions/{id}`
- `POST /agent/sessions/{id}/messages`
- a draft or in-progress streaming path design at `POST /agent/sessions/{id}/messages/stream`

The web UI is already moving toward incremental agent rendering. Keeping both the synchronous endpoint and the streaming endpoint would preserve compatibility, but it also preserves duplicate persistence logic and duplicate correctness risk.

The user explicitly approved a breaking change if that simplifies the system.

## Scope

### Included

- make streaming the only supported session-chat write path
- remove the old synchronous session-message endpoint
- use the streaming endpoint from the web UI
- simplify tests and session persistence around one canonical path
- update docs and tests to reflect the breaking change

### Excluded

- changes to `/agent/run`
- changes to autonomy endpoints
- websocket transport
- compatibility shims for old clients beyond clear failure behavior

## Design Goals

- keep only one session-chat write contract
- reduce session persistence complexity
- reduce the risk of divergent session histories across sync and streaming paths
- make front-end and back-end semantics line up cleanly

## Recommended Approach

The revised approach is:

1. keep `POST /agent/sessions/{session_id}/messages/stream`
2. remove `POST /agent/sessions/{session_id}/messages`
3. update the front end to use streaming only
4. remove synchronous endpoint expectations from tests

This is a deliberate breaking change and should be treated as such.

## API Contract

### Kept

- `POST /agent/sessions`
- `GET /agent/sessions/{session_id}`
- `POST /agent/sessions/{session_id}/messages/stream`

### Removed

- `POST /agent/sessions/{session_id}/messages`

The streaming endpoint remains the sole session-chat write path.

## Streaming Protocol

The event protocol remains the same minimal four-event model:

- `start`
- `delta`
- `final`
- `error`

The front end should continue to treat `final` as the only successful terminal event and `error` as the failure terminal event.

## Persistence Rules

Streaming remains the only place where assistant replies are persisted for session chat.

Rules:

- append the user message to the session context for the in-flight run
- accumulate assistant text in memory during streaming
- on successful completion, write one final assistant message to session history
- on failure, do not persist a fake successful assistant message

With the synchronous path removed, there is only one write path to audit and test.

## Error Behavior For Removed Endpoint

The removed endpoint should not silently remain operational.

Preferred behavior:

- remove the route entirely, so old callers receive `404`

Alternative acceptable behavior:

- keep a stub that returns a clear `410 Gone` or `400` with a migration message

The simpler option is removing the route entirely unless there is a strong need for migration messaging.

## Front-End Behavior

The web UI should use only the streaming path.

Behavior:

1. create or reuse session
2. append user bubble immediately
3. create assistant placeholder on `start`
4. append deltas incrementally
5. finalize on `final`
6. mark error or aborted state on `error` or abnormal EOF

No fallback code path should call the old synchronous message endpoint.

## Testing Strategy

### Back-End Tests

Back-end tests should focus on:

- event ordering
- final persistence
- error handling
- removed synchronous route behavior

The previous synchronous endpoint success-path tests should be deleted or rewritten.

### Front-End Tests

Front-end tests should focus on:

- streaming helper presence
- placeholder lifecycle
- delta accumulation
- terminal `final` handling
- `error` / EOF handling

### Regression Tests

Regression scope should now be narrower and clearer:

- stream is the only supported chat write path
- `/agent/run` still behaves independently

## Acceptance Criteria

This revised design is complete when all of the following are true:

1. the old synchronous session-message endpoint is removed or explicitly deprecated with failure behavior
2. the web UI sends session chat only through the streaming endpoint
3. session history is persisted only through the streaming path
4. stream success and failure behavior are fully covered by tests
5. old sync-endpoint tests are removed or updated to reflect the breaking change

## Final Recommendation

Do not preserve the old synchronous session-message path if the product direction is now streaming-first.

Removing it is the simplest way to reduce code duplication, eliminate one whole class of session-history drift bugs, and keep the browser and server on one contract.
