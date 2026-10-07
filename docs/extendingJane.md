# Extending Jane

Jane is big, but new work does not need to start in `bot.py`.

The easy rule is:

- Discord commands and buttons go in `cogs/`
- reusable feature logic goes in `features/`
- shared runtime plumbing goes in `runtime/`

Here are the call points that are already meant to be reused.

## Review Workflows

`features.staff.workflows` is the shared state/history engine behind applications, ribbons, projects, ORBAT requests, LOAs, and ANRD payments.

A new feature can register a `WorkflowDefinition`, then call:

- `ensureRun(...)`
- `transitionSubjectRun(...)`
- `listPendingRuns(...)`
- `listRunEvents(...)`

If the feature already has database rows with a status field, `WorkflowSubjectBridge` handles the repetitive ID, metadata, state, summary, and reconciliation work. Existing `workflowBridge.py` files are small examples worth copying.

Definitions can be registered from an optional plugin during setup:

```python
from features.staff.workflows import registerWorkflowDefinition

registerWorkflowDefinition(MY_WORKFLOW)
```

## Small Daily Message Flows

`runtime.dailyMessage.DailyMessageTrigger` handles the exact-user, exact-channel, once-per-calendar-day pattern. It keeps its day claim in `bot_settings`, survives restarts, and releases the claim if Discord cannot send.

Create one trigger and call its `handle(message)` method from message routing. Potato's small greeting in `runtime/textCommands.py` is the first example.

## Identity

`features.community.identity.service` owns the reusable identity calls. The Discord cog and HTTP callback both use it.

Useful entry points include:

- `createLinkAttempt(...)`
- `completeRobloxOAuth(...)`
- `applyMemberVerification(...)`
- `apiIdentityByDiscordId(...)`
- `listStoredIdentityLinks(...)`

Keep OAuth and role logic there instead of teaching a new cog its own version.

## Recruitment And Sheets

Recruitment database calls live in `features.staff.recruitment.service`. Sheet rules, row placement, and batch writes live in `features.staff.recruitment.sheets` and `outputs`.

The sheet functions are regular callable functions, but they are blocking Google API work. From an async Discord flow, run them through `runtime.taskBudgeter.runInteractiveSheetsThread(...)` or the background equivalent.

## Runtime Building Blocks

- `runtime.taskSupervisor.TaskSupervisor` owns background tasks and cleans them up.
- `runtime.retryQueue.RetryQueue` accepts new job handlers with `registerHandler(...)`.
- `db.sqlite.runWriteTransaction(...)` is the safe boundary for multi-step SQLite changes.
- `features.operations.serverSafety` exposes snapshot and preview service calls.
- `runtime.extensionLayout` loads normal cogs plus optional public/private plugin extensions.

These are intentionally plain Python callables. A future cog, maintenance job, HTTP route, or sibling bot can reuse the service without pretending to be a Discord interaction.

## A Small Warning

Do not make a generic abstraction just because two functions look vaguely alike. Pull out the boundary when the same real flow needs a second caller, keep Discord UI at the edge, and leave the feature service usable on its own. Jane has enough clever history already.
