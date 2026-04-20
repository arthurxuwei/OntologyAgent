## Overview

The agent currently has emergency mitigations for streaming tool-call failures:

- invalid persisted tool messages are dropped from session history
- if `astream()` fails with `ToolMessage.tool_call_id` validation, the endpoint falls back to `ainvoke()`
- if the fallback still returns an empty assistant message, the endpoint uses already-streamed deltas as a last-resort final output

Those mitigations keep the user experience alive, but they do not solve the underlying problem. The real issue is that the provider-backed streaming tool-call path can produce an invalid `ToolMessage` state where `tool_call_id` is missing.

## Problem Statement

Current facts established by debugging:

- the agent does not explicitly construct `ToolMessage` itself in the affected path
- the failure occurs inside the `create_react_agent(...).astream(..., stream_mode=["messages", "values"])` pipeline
- the validation error is: `ToolMessage.tool_call_id` must be a string, but `None` appears
- the configured model provider is OpenAI-compatible via `OPENAI_BASE_URL=https://www.packyapi.com/v1`
- `ainvoke()` does not trigger the same `ToolMessage` validation failure, though it may still return an empty assistant response in some cases

This strongly suggests that the unstable part is the provider-aware streaming tool-call path, not the basic non-streaming tool execution path.

## Goals

- Remove reliance on unstable streaming tool-call message construction for providers that do not support it cleanly
- Preserve streaming for ordinary assistant text deltas when safe
- Keep session history semantically correct without depending on invalid `ToolMessage` recovery
- Reduce fallback-only behavior and move to a deliberate provider-aware execution mode

## Non-Goals

- Replacing LangGraph or LangChain agent architecture entirely
- Building a generic tool-call event protocol for all providers in V1
- Supporting partial streamed tool-call internals in the frontend
- Removing the current mitigations immediately before the safer path is verified

## Considered Approaches

### Approach 1: Recommended

Introduce a provider-aware execution mode where OpenAI-compatible providers known to have unstable streamed tool calls do not use streaming tool execution. Instead, the endpoint uses a non-streaming agent invocation for tool-using turns and only streams plain assistant text when safe.

Pros:

- Solves the root problem at the integration boundary instead of patching corrupted messages later
- Keeps tool-call semantics in the stable `ainvoke()` path
- Minimizes assumptions about provider chunk fidelity

Cons:

- Reduces token-by-token streaming during tool-using turns for those providers

### Approach 2: Reconstruct missing `tool_call_id` locally

Intercept malformed stream chunks and synthesize a stable `tool_call_id` when it is missing.

Pros:

- Preserves fully streamed tool execution behavior

Cons:

- Dangerous because it assumes message correlation semantics the provider may not actually guarantee
- Easy to create mismatched tool-result attribution

### Approach 3: Keep current fallbacks indefinitely

Continue relying on session sanitization, `ainvoke()` fallback, and delta-based final-output salvage.

Pros:

- No additional work in the short term

Cons:

- Root problem remains
- Behavior depends on multiple layered mitigations
- Harder to reason about future regressions

## Chosen Design

Implement a provider-aware streaming mode selection.

For providers/endpoints that are known to have unstable streamed tool-call behavior, the agent should avoid `astream()` for tool-using execution and instead run the turn through `ainvoke()`. The frontend can still receive a streamed response wrapper, but the server will emit a single `final` event for that turn instead of attempting unreliable streamed tool-call progression.

The current mitigation code remains in place temporarily as defense in depth until the provider-aware mode is verified.

## Architecture

### 1. Provider Capability Gate

Add a narrow runtime decision based on provider configuration.

Inputs already available:

- `OPENAI_BASE_URL`
- model name

V1 policy:

- if the configured provider is the known OpenAI-compatible gateway currently in use (`packyapi`), treat streamed tool execution as unsupported
- for that provider, the streaming session endpoint should bypass `graph.astream()` for tool-executing turns and use `graph.ainvoke()` instead

This should be expressed as an explicit capability decision, not as ad-hoc string checks spread through the code.

### 2. Stream Endpoint Behavior

`/agent/sessions/{session_id}/messages/stream` keeps the same external API.

Behavior changes for unsupported streamed-tool providers:

- emit `event:start`
- run the agent turn through `ainvoke()` directly
- extract final output from the returned messages
- persist sanitized session history
- emit `event:final`

This means tool-using turns are no longer token-streamed, but they are stable.

### 3. Safe Streaming for Text-Only Providers or Paths

The existing `astream()` path can still be used where streaming is safe. The capability gate decides whether that path is allowed.

The current `ToolMessage` validation fallback remains as a temporary safeguard, but it should become a rare backup path rather than the primary way the system stays alive.

### 4. Session History Semantics

Session persistence should continue to sanitize invalid historical tool messages. Even after the provider-aware fix, this remains worthwhile as backward protection for previously corrupted sessions.

## V1 Scope

Included in V1:

- provider-aware detection for unstable streamed tool execution
- stable non-streaming execution path for those providers inside the stream endpoint
- tests proving that the endpoint returns `final` without relying on malformed `ToolMessage` recovery for that provider mode

Explicitly excluded from V1:

- synthesizing `tool_call_id`
- parsing and rendering tool-call chunk internals in the frontend
- full provider capability negotiation across many vendors

## Failure Handling

### Unsupported Streamed Tool Execution

This is no longer treated as an unexpected runtime accident. It becomes a known provider capability limitation. The endpoint should proactively choose the stable execution mode.

### Empty Final Assistant Output

The existing fallback text behavior may remain, but once tool-using turns are routed through a stable provider-aware mode, empty outputs should be much rarer and easier to attribute to the model itself rather than malformed tool-call messages.

### Legacy Corrupted Sessions

Old session history may still contain invalid tool messages. Session sanitization should remain in place to avoid replaying broken state.

## Testing Strategy

Add tests that cover:

- provider capability selection when `OPENAI_BASE_URL` points to the known OpenAI-compatible gateway
- stream endpoint choosing `ainvoke()` directly under that provider mode instead of entering the streamed tool-call path
- session history persistence still working under that mode
- existing stream behavior remaining available for providers that are not gated off

## Summary

The correct root fix is not to keep patching malformed `ToolMessage` objects after they appear. The correct fix is to recognize that the current OpenAI-compatible provider does not produce reliable streamed tool-call state and to route those turns through a stable non-streaming execution path. That addresses the failure at the provider integration boundary while keeping the existing fallback logic only as temporary defense in depth.
