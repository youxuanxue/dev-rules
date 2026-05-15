---
approved_by: pending
status: pending_review
risk_level: high
---

# twin runtime reentry and watchdog approval

## Intent

`twin` must not rely on a remembered interactive Claude Code session to continue after a worker finishes or becomes stale. Workspace artifacts are the source of truth for reentry.

## Approved behavior pending review

- `/twin <workspace>` starts from artifact state, not conversation memory.
- `python3 -m scripts.twin next --workspace <ws> --json` returns the next deterministic action for the current workspace state.
- `worker_running` with an existing `run.json` routes to supervisor review.
- `worker_running` with no worker artifacts, or no `current_run_id`, routes to fresh worker recovery using the existing `next_instruction` path.
- `worker_running` that is still starting, active, or quiet routes to bounded `watch`.
- `python3 -m scripts.twin watch --workspace <ws> --max-wait-seconds N --poll-interval-seconds N --json` waits only until a non-watch action appears or the bounded timeout expires.
- `watch` timeout returns `worker_quiet_timeout` and does not kill processes, delete artifacts, or mutate workspace state.
- `status` remains read-only and may only display compact worker diagnostics derived from artifact metadata.

## Non-goals

- No long-running daemon.
- No UI/TUI.
- No process killing or orphan-process cleanup.
- No state schema version change.
- No destructive artifact repair from `status`.

## Validation

- `python3 -m scripts.twin validate --fixtures`
- `./scripts/preflight.sh`
