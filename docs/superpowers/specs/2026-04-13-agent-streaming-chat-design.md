# Agent Streaming Chat Design

## Summary

This spec adds a streaming chat path for the agent session UI.

The current implementation waits for the full assistant reply before returning JSON to the browser, which makes the UI feel static and hides useful provider behavior. The goal of this change is to keep the existing synchronous endpoint for compatibility while adding a dedicated streaming endpoint and a front-end rendering path that shows assistant output incrementally.

This work only covers session chat. It does not change `/agent/run`, autonomy actions, or the broader observability dashboard behavior.

## Current Context

The current system already has:

- `POST /agent/sessions`
- `GET /agent/sessions/{id}`
- `POST /agent/sessions/{id}/messages`

The browser creates a session, sends `{ input }` to the message endpoint, waits for the JSON response, and then appends a completed assistant bubble using `payload.output`.

This works functionally, but it has two limitations:

- the user sees no incremental output while the model is generating
- provider or adapter behavior is harder to observe during generation

The new streaming path should solve that while preserving the old endpoint.

## Scope

### Included

- add a dedicated streaming session-message endpoint
- stream agent output incrementally to the browser
- add a front-end assistant placeholder that updates during the stream
- keep final session persistence consistent with the fully assembled assistant output
- preserve the existing non-streaming session-message endpoint

### Excluded

- changes to `/agent/run`
- changes to `/autonomy/*`
- websocket support
- streaming tool execution details beyond assistant text deltas
- protocol changes for non-browser clients that still use the synchronous endpoint

## Design Goals

- let the user see assistant output as it is generated
- keep session history aligned with what the user actually saw
- preserve existing API compatibility
- keep the protocol small and explicit
- make failures visible without leaving fake successful empty messages in history

## Recommended Approach

Three approaches were considered:

1. dedicated streaming endpoint using SSE-style events
2. polling-based pseudo-streaming
3. websocket chat transport

The recommended approach is the dedicated streaming endpoint.

It gives the browser a natural way to consume incremental output without changing the existing synchronous endpoint and without introducing websocket lifecycle complexity.

## API Design

Add a new endpoint:

- `POST /agent/sessions/{session_id}/messages/stream`

Request body:

```json
{
  "input": "..."
}
```

Response type:

- `text/event-stream`

The existing synchronous endpoint remains unchanged:

- `POST /agent/sessions/{session_id}/messages`

## Event Protocol

The streaming endpoint should emit a minimal event set.

### `start`

Sent once at the beginning.

```text
event: start
data: {"sessionId":"...","input":"..."}
```

### `delta`

Sent zero or more times as assistant text grows.

```text
event: delta
data: {"text":"你好"}
```

### `final`

Sent once when the full assistant reply is complete and persisted.

```text
event: final
data: {"sessionId":"...","output":"你好，我来帮你看一下。","messageCount":2}
```

### `error`

Sent once if generation fails.

```text
event: error
data: {"message":"..."}
```

No additional event types are required for the first version.

## Back-End Behavior

### Streaming Path

The new streaming endpoint should:

1. load the session
2. append the user message to the in-flight prompt context
3. call the graph in streaming mode
4. accumulate assistant text chunks in memory
5. emit `delta` events as text arrives
6. once complete, persist the final assembled assistant message into `session.messages`
7. emit a `final` event with the final text and updated `messageCount`

### Persistence Rule

Do not persist partial assistant chunks during streaming.

Instead:

- keep an in-memory `collected_text`
- write a single final assistant message once the stream completes successfully

This keeps session history clean and consistent.

### Error Rule

If streaming fails:

- do not persist a partial assistant message as a normal successful reply
- emit an `error` event
- leave session history unchanged for the assistant side of that turn

If the stream reaches a completed but empty assistant output and the existing fallback rule applies, the persisted assistant message and `final` event should use the same fallback text.

## Front-End Behavior

### Message Lifecycle

When the user sends a message:

1. append the user bubble immediately
2. create an assistant placeholder bubble
3. open the streaming request
4. update the placeholder text as `delta` events arrive
5. on `final`, solidify the bubble and update session metadata
6. on `error`, convert the placeholder into an error-state bubble or show a nearby system message

### UI Rules

- the placeholder should exist before the first delta arrives
- the placeholder should never remain visually blank after an error; it should switch to an explicit error state or be replaced with an error message
- the final rendered assistant text should exactly match the text persisted in session history

## Streaming Transport Choice

Use browser `fetch()` with a readable stream rather than `EventSource`.

Reason:

- the request needs to remain a `POST` with JSON body
- `EventSource` only supports `GET`

The front-end should read the `ReadableStream`, parse SSE-style frames, and dispatch `start`, `delta`, `final`, and `error` events locally.

## Session Consistency

The final text shown in the UI and the final text stored in session history must be identical.

This means:

- the stream accumulator is the source for the persisted assistant message
- the `final` event must carry the same assembled output that gets stored

This rule is especially important because the current codebase already had to handle empty assistant replies and fallback text alignment.

## Error Handling

Errors to handle explicitly:

- unknown session id
- agent invocation failure
- malformed stream chunk
- stream interruption before completion
- empty final output with fallback

The first version does not need reconnect support or resumable streams.

## Testing Strategy

### Back-End Tests

Add tests for the new streaming endpoint that validate:

- `start -> delta -> final` ordering for a successful run
- `error` event for failed generation
- final session persistence uses the complete assembled output
- empty final output uses the same fallback text rule as the synchronous endpoint

### Front-End Tests

Add tests or page-source/behavior verification for:

- assistant placeholder is created before deltas arrive
- delta chunks append incrementally
- final event solidifies the message
- error event does not leave an empty successful-looking assistant bubble

### Regression Tests

Keep the existing synchronous session-message path working.

That means tests should confirm:

- `POST /agent/sessions/{id}/messages` still returns the old JSON response
- streaming support does not break existing session state behavior

## Acceptance Criteria

This feature is complete when all of the following are true:

1. a new streaming session-message endpoint exists
2. the browser displays assistant text incrementally during generation
3. the browser still supports the existing session creation and chat flow
4. successful stream completion persists exactly the final displayed assistant text
5. failed streams do not persist partial assistant replies as successful messages
6. empty final replies use the existing fallback behavior consistently
7. the old synchronous message endpoint still works unchanged

## Final Recommendation

Add streaming as a dedicated session-chat path instead of replacing the synchronous API or moving to websockets.

That gives the UI the responsiveness it needs while keeping the protocol simple, preserving compatibility, and avoiding unnecessary transport complexity.
