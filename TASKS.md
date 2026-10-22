# Background tasks

Main Ken handles conversation and task controls. It has local file tools for
memory/configuration and four MCP tools: `task_start`, `task_list`, `task_details`,
and `task_control`. Execution tools belong to independent worker sessions, with
four worker slots separate from the main chat lock. Every main turn receives a
fresh task snapshot. Scheduled jobs use the same task manager.

## State and control

SQLite tables `tasks`, `task_attempts`, and `task_commands` in `app.db` store stable
task IDs, revisions, attempt history, results, and early SDK session checkpoints.
Progress travels over the existing event/WebSocket stream. Worker transcripts
stay out of chat; main Ken receives coalesced result notices and speaks to the
human. Human messages interrupt automatic announcements and take priority.

`steer`, `stop`, and `pause` record a new revision and return immediately. The old
attempt cannot report against that revision. It is interrupted and closed before
a replacement starts or a stop is acknowledged. `applied_revision` changes when
the worker calls `task_checkpoint`, or after a stop/pause finishes. HTTP controls
and the main MCP control tool require the revision they observed, rejecting stale
controls. Repeated task creation with the same originating message and brief is
deduplicated.

`resume` continues the saved worker session. `retry` uses a fresh session under
the same task, retaining files, prior results, and instructions. `review` uses a
fresh session to inspect existing work against its criteria. Work reports become
`needs_review`; only a review reporting `passed` with evidence becomes `done`.
Missing reports get up to two continuation prompts, then stay unverified.
`waiting` brings the worker's question back through main Ken.

On restart, recorded stops/pauses settle accordingly. In-flight work becomes
`interrupted` and waits for a resume request; queued work can start. Stop does not
undo file edits or external actions, and resume must reconcile those effects
before repeating anything. The engine drains workers on SIGTERM or normal exit.

## Workspaces and shared resources

Tasks default to private folders in `~/.ken/tasks/<id>/work`. A repository path
creates a detached worktree in `<id>/code` from the source's committed HEAD;
uncommitted source changes are not copied. Results stay there for inspection and
explicit integration. Non-Git folders use a shared folder lease. Named resources
(including `browser`) are serialized, with leases held until worker teardown.
Workers waiting for resources do not consume execution slots.

These are coordination boundaries, not filesystem sandboxes. Workers still run
as the Ken user. Browser work requires the browser lease and should use its own
tabs; integrations affecting other shared state should name their resource too.

Ken disables the SDK's nested background-task feature so a model cannot finish
its turn while a shell job silently keeps running. Foreground command timeouts
can be set up to one hour; the engine's existing 45-minute inactivity watchdog
still applies. See the SDK host's [environment variable reference](https://code.claude.com/docs/en/env-vars).

## Validation

Run `tests/check.sh`. `tests/test_tasks.py` covers independent chat capacity,
steering fences, early controls, pause/resume/retry, review evidence, resource
leases, worktrees, restart recovery, HTTP conflicts, and notification priority.
`tests/test_brains.py` covers early session checkpoints and SDK error results.

For a live-model check, ask main Ken for two independent local file tasks, steer
only one while both run, and review both outputs. Also stop a task during a
foreground command and verify that the process exits without performing its
remaining work. Exercise task cards at desktop/mobile widths and in both themes.
