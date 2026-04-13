# Agent Empty Reply Diagnostics Design

## Summary

This spec adds targeted backend diagnostics for one specific failure mode:

the agent session endpoint receives an `AIMessage` whose final content is empty, causing the UI to show an empty or fallback response even though the model call technically completed.

The current code already returns a fallback message for empty replies, but the root cause is still hard to diagnose because the server logs do not capture enough context about the final message object or provider metadata. This spec adds a focused warning log for that branch only.

## Current Context

The current `agent/main.py` flow is:

- `_invoke_agent()` calls the graph
- `send_agent_session_message()` stores messages and calls `_extract_final_output()`
- `_extract_final_output()` normalizes the final AI message content
- if the normalized output is empty, the code now returns a fallback text

What is missing is observability for why the content was empty in the first place.

Recent investigation showed a concrete real-world case where:

- upstream provider returned an assistant message with `content = null`
- LangChain surfaced that as `AIMessage(content='')`
- the endpoint returned an empty string until fallback handling was added

That means the right next step is not changing the HTTP response shape or making a second model call. The right next step is logging enough context at the empty-reply branch to diagnose provider behavior later.

## Scope

### Included

- add a warning log when the final extracted agent output is empty
- include model and provider configuration context in that log
- include final message metadata and lightweight message-chain summary
- add a test that proves the warning is emitted for empty replies

### Excluded

- changing API response shape
- adding health endpoint fields
- logging every normal model response
- replaying or reissuing provider calls for diagnostics
- changing provider or model selection in this task

## Design Goals

- make empty-reply failures diagnosable from server logs
- avoid log spam on healthy requests
- preserve the current fallback user-facing behavior
- avoid logging full prompts or full message history content
- keep the implementation small and isolated to the empty-reply branch

## Recommended Approach

Three approaches were considered:

1. empty-reply-only diagnostic log
2. log all agent invocations with full metadata
3. perform a second raw provider call when content is empty

The recommended approach is the empty-reply-only diagnostic log.

It gives enough evidence for diagnosis while keeping the normal path quiet and avoiding extra model cost or duplicate side effects.

## Logging Placement

The correct place for this diagnostic is around `_extract_final_output()`.

Reasons:

- the code already has the final selected AI message at that point
- the code can directly compare raw content with normalized output
- the branch where output becomes empty is known exactly there
- it avoids adding noise to `_invoke_agent()` for successful runs

The log should only trigger when the normalized final output is empty.

## Logged Fields

The warning log should include the minimum fields needed to diagnose provider or adapter behavior.

### Provider And Model Context

- `BRAIN_AGENT_MODEL`
- resolved `OPENAI_BASE_URL` or equivalent base URL helper output

### Final Message Context

- final message Python type name
- final message logical type such as `ai`
- `repr(final_message.content)`

### Provider Metadata

- `response_metadata`
- `additional_kwargs`

### Message Chain Summary

- total message count
- final message index or position if easy to compute
- tail message types, for example the last three message `type` values

This is intentionally summary-level. Do not log the full text of all prior messages.

## Logging Level And Style

Use a standard module logger:

```python
logger = logging.getLogger(__name__)
```

Use warning level:

```python
logger.warning(...)
```

This is appropriate because:

- the user-visible response degraded
- the request still completed
- the server did not crash

## Data Safety Boundaries

Do not log:

- full user prompts
- full system prompt
- full tool payloads
- full message history text

Do log:

- structural metadata
- the raw final content representation
- provider metadata fields already returned on the final message object

This keeps the log useful without turning it into a transcript dump.

## Test Strategy

Add one focused regression test that:

- constructs an `AIMessage(content="")`
- includes representative `response_metadata`
- includes representative `additional_kwargs`
- passes it through the extraction path

The test should assert:

1. fallback text is returned
2. a warning log entry is emitted
3. the log includes key diagnostic fields such as model, base URL, and response metadata markers

This test should not require a real model call.

## Acceptance Criteria

This change is complete when all of the following are true:

1. empty final AI replies still return the existing fallback text
2. the backend emits a warning log for empty final replies
3. the warning log includes:
   - model name
   - base URL
   - final content representation
   - `response_metadata`
   - `additional_kwargs`
   - message count or tail message type summary
4. normal non-empty replies do not emit this warning
5. the implementation does not change API response shape
6. the implementation does not make any extra model calls

## Final Recommendation

Do not try to solve this by adding broad debug logging everywhere or by replaying upstream requests.

Instrument the exact branch where empty output is detected.

That turns an opaque provider quirk into a diagnosable warning without increasing cost or disturbing normal agent behavior.
