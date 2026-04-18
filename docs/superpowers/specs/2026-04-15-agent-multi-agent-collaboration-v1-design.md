# Agent Multi-Agent Collaboration V1 Design

## Summary

This spec defines the first product-facing multi-agent collaboration model for the repository.

The goal is not to turn the existing chat UI into a raw multi-agent group chat. The goal is to let the primary agent decompose a user request into explicit tasks, assign those tasks to child agents, and present only structured summaries back to the user.

The approved V1 product shape is:

- product capability priority: multi-agent collaboration first
- user experience shape: hybrid manager-style collaboration
- user visibility: summary only, not raw child-agent transcripts or reasoning
- child-agent source: mixed built-in and dynamic agents
- dynamic agent flexibility: full custom definition, but still governed by platform policy
- approval model: tiered policy
- task decomposition depth: single-level dispatch only
- task lifecycle: `draft | awaiting_confirmation | queued | running | done | failed | cancelled`

This design also sets the foundation for a later task-execution loop without trying to build the full execution-closure feature in the same change.

## Current Context

The current repository already has:

- a primary agent session model in `agent/main.py`
- a streaming-only session chat path
- an existing web console in `agent/web/chat.html`
- autonomy-oriented child-agent concepts for wealth management, but not a general productized multi-agent task model

What is missing is a general user-facing collaboration layer where:

- the primary agent can explicitly break work into tasks
- those tasks can be assigned to child agents
- the UI can show structured progress and outcomes
- riskier dispatches can be held behind confirmation

Right now, child-agent style behavior is not exposed as a clear first-class product contract.

## Scope

### Included

- add explicit task-oriented collaboration as a product capability
- let the primary agent decide whether to answer directly or create tasks
- support built-in and dynamic child-agent profiles
- support single-level dispatch from the primary agent to child agents
- add tiered confirmation rules for risky task execution
- expose task summaries, status, and final outcomes in the UI
- keep the existing single primary chat experience intact

### Excluded

- recursive or unbounded agent hierarchies
- raw child-agent transcript visibility in the UI
- a multi-speaker group-chat interface
- cross-session durable workflow recovery
- a full workflow engine
- unrestricted prompt-only tool authorization
- long-lived artifact storage beyond lightweight structured results

## Design Goals

- keep the user talking to one primary agent
- make task decomposition explicit and inspectable
- let child agents do bounded work without becoming independent user-facing actors
- support dynamic agent creation without making prompt text the sole authority for permissions
- allow risky work to pause for explicit user confirmation
- preserve a clean path for future execution-closure work

## Approaches Considered

### 1. Task-Driven Manager Layer

The primary agent creates explicit tasks, optionally assigns them to child agents, and reports structured summaries back to the user.

Pros:

- best fit for the approved hybrid UX
- keeps one clear user-facing voice
- makes later execution-closure work easier because tasks already exist as first-class objects

Cons:

- requires introducing new product objects beyond chat messages
- requires clear boundaries between primary-agent and child-agent responsibilities

### 2. Event-First Collaboration Bus

Treat collaboration primarily as a stream of internal events and derive task views from those events.

Pros:

- flexible for auditing and replay
- good long-term platform shape

Cons:

- too infrastructure-heavy for the immediate product need
- risks building the plumbing before the product contract is clear

### 3. Agent Registry First

Start with child-agent templates, dynamic-agent creation, and capability registration, then add task flow on top.

Pros:

- makes agent roles explicit early
- useful for future expansion of agent types

Cons:

- solves agent identity before solving user task completion
- weaker direct user value in the first increment

## Recommended Approach

Use the task-driven manager layer.

This keeps the current product mental model intact: the user still talks to one primary agent. The new collaboration capability appears as explicit task planning and assignment under that primary conversation, not as a noisy multi-party chat.

The implementation may internally emit events, but the first-class product model should be tasks, dispatch records, and summary events rather than a general-purpose event bus.

## Core Objects

V1 introduces four first-class product objects.

### Task

Represents one primary-agent-defined unit of work.

Suggested fields:

- `taskId`
- `sessionId`
- `title`
- `summary`
- `status`: `draft | awaiting_confirmation | queued | running | done | failed | cancelled`
- `ownerAgentId`
- `parentTaskId`
- `resultSummary`
- `requiresConfirmation`
- `riskLevel`

V1 rule:

- `parentTaskId` should always be empty because recursive dispatch is out of scope

### AgentProfile

Represents a callable child-agent definition.

Suggested fields:

- `agentId`
- `kind`: `builtin | dynamic`
- `name`
- `responsibility`
- `systemPrompt`
- `toolPolicy`
- `approvalPolicy`

### Dispatch

Represents one assignment from the primary agent to one child agent.

Suggested fields:

- `dispatchId`
- `taskId`
- `assignedAgentId`
- `dispatchReason`
- `inputSummary`
- `status`

### TaskEvent

Represents a UI-safe summary event.

Suggested fields:

- `eventId`
- `taskId`
- `type`: `task_created | confirmation_requested | dispatched | started | completed | failed | cancelled`
- `summary`
- `createdAt`

These events are for status display and audit summaries. They are not raw chain-of-thought or raw child-agent transcript records.

## Collaboration Flow

The primary happy path for V1 is:

1. the user sends a request to the primary agent
2. the primary agent decides whether the request needs decomposition
3. if not, the primary agent handles the request directly through the existing single-agent path
4. if yes, the primary agent creates one or more `draft` tasks
5. each task is classified for risk and confirmation requirements
6. low-risk tasks move to `queued`
7. high-risk tasks move to `awaiting_confirmation`
8. once allowed, the primary agent chooses or creates a child agent and creates a dispatch record
9. the child agent executes only its assigned task
10. the child agent returns structured results to the primary agent
11. the primary agent synthesizes the final user-facing response

If multiple tasks exist, confirmation should be per task, not all-or-nothing for the whole request.

## Risk Levels

V1 should classify tasks into three levels.

### Low Risk

Examples:

- research
- summarization
- comparison
- other read-only analytical work

Behavior:

- the primary agent may auto-create or choose a child agent
- execution may continue automatically

### Medium Risk

Examples:

- bounded tool usage that prepares information or proposals
- cross-system data gathering without external state mutation

Behavior:

- the primary agent may auto-dispatch
- the UI should expose the intended capability scope clearly

### High Risk

Examples:

- chain submissions
- trades
- bot start or stop actions
- file or configuration writes
- destructive or externally mutating operations

Behavior:

- execution must stop at `awaiting_confirmation`
- no real dispatch execution should occur before approval

## Confirmation Rules

Confirmation belongs to tasks, not to free-form chat messages.

Each task should carry:

- `riskLevel`
- `requiresConfirmation`
- `confirmationReason`

The confirmation payload shown to the user should be structured and concise:

- task name
- assigned agent name
- purpose
- tool scope
- affected targets or systems
- why confirmation is required

State transitions:

- `draft -> awaiting_confirmation`
- `awaiting_confirmation -> queued` on user approval
- `awaiting_confirmation -> cancelled` on user rejection

Rejected tasks should not block the primary agent from summarizing completed low-risk work and offering next steps.

## Dynamic Agent Creation Constraints

The approved product direction allows full custom dynamic-agent definitions, but V1 must still enforce platform boundaries.

### Identity Constraints

- dynamic agents must have a `name` and `responsibility`
- display identity does not imply elevated privilege
- reserved platform identities should not be impersonable

### Prompt Constraints

- dynamic agents may define a custom `systemPrompt`
- the platform must wrap that prompt with a non-removable guardrail layer

That guardrail layer should require at least:

- no privilege escalation
- no creation of lower-level child agents
- no direct final-user answer ownership
- structured task-result output

### Tool Constraints

- requested tools are not automatically granted
- the effective permission set must come from validated `toolPolicy`
- prompt text alone must never authorize additional tools

### Execution Constraints

- a child agent runs only against one task at a time
- the child agent returns structured output only

Suggested return shape:

- `resultSummary`
- `artifacts`
- `failureReason`
- `recommendedNextStep`

## UI Design

V1 should preserve the current streaming-first single-chat experience and add a task-summary layer.

### Primary Conversation

- the user continues talking only to the primary agent
- the primary agent explains when it is decomposing work, waiting for confirmation, or summarizing child-agent outcomes
- child agents do not speak directly in the main transcript

### Task Summary List

Each task should appear as a compact card with:

- title
- assigned agent
- current status
- short purpose summary
- final result summary or failure reason

For `awaiting_confirmation` tasks, the card should also expose approval and cancellation actions.

### Task Detail View

The detail view should remain summary-only and show:

- dispatch reason
- tool scope
- risk level
- result summary
- artifact summary
- event timeline

The UI should not show raw child-agent internal transcripts or chain-of-thought-like content.

## Responsibility Boundaries

### Primary Agent Responsibilities

- understand the user request
- decide whether to decompose work
- create tasks
- classify task risk
- decide whether confirmation is needed
- choose or create child agents
- synthesize child-agent results
- produce the final user-facing answer

### Child Agent Responsibilities

- execute exactly one assigned task
- use only permitted tools and policies
- return structured results
- avoid direct ownership of the final user response
- never create more child agents in V1

### Platform Responsibilities

- persist `Task`, `AgentProfile`, `Dispatch`, and `TaskEvent`
- enforce permission validation
- enforce confirmation gates
- expose summary-safe updates for the UI

## V1 Non-Goals

V1 explicitly does not include:

- recursive dispatch
- raw multi-agent group chat
- child-agent transcript visualization
- a full workflow engine
- durable long-lived task recovery across sessions
- rich artifact management beyond lightweight structured summaries
- fully open-ended tool authorization

## Testing Strategy

Testing should be split across model, workflow, and UI layers.

### Back-End Tests

- task creation and lifecycle transitions
- per-task confirmation gates
- primary-agent direct answer path when no decomposition is needed
- single-level dispatch enforcement
- dynamic-agent policy validation
- structured child-agent result handling

### UI Tests

- task cards render and update correctly
- confirmation actions only appear for gated tasks
- child-agent summaries do not leak raw transcripts
- main chat transcript remains primary-agent-only
- streaming chat remains functional while task status updates occur

### Regression Tests

- existing streaming session-chat behavior remains intact
- existing autonomy endpoints keep their current behavior unless explicitly integrated later

## Suggested Delivery Order

Recommended implementation order:

1. add the core data model and persistence shapes
2. add the primary-agent task and dispatch decision layer
3. add UI task summaries and confirmation controls
4. add dynamic-agent creation with policy validation

This sequence delivers visible product value early while keeping policy-sensitive features behind a more stable base.

## Acceptance Criteria

This design is complete when all of the following are true:

1. the primary agent can choose between direct answering and task decomposition
2. decomposed work is represented as explicit tasks with the approved lifecycle states
3. the primary agent can dispatch tasks to built-in or dynamic child agents
4. child-agent collaboration is limited to one dispatch level
5. high-risk tasks require explicit confirmation before execution
6. the user sees task summaries, state, and outcomes without seeing raw child-agent transcripts
7. dynamic agents can be fully customized in definition but remain bounded by validated policy
8. the primary agent remains the only final user-facing speaker in the main conversation flow

## Final Recommendation

Do not start by building a visible multi-agent chat room or a general workflow engine.

Start by making collaboration task-oriented, summary-first, and primary-agent-led.

That delivers real multi-agent product value without sacrificing control, clarity, or the existing streaming chat experience.
