# US-088-twin-universal-runtime

- ID: US-088
- Title: Universal twin host and worker runtime
- As a / I want / So that: As a twin operator, I want Claude, Codex, Antigravity, and local CLI workers to share one deterministic runtime, so that I can change providers without running a second supervisor service or forking workspace state.
- Trace:
  - `docs/approved/twin-team-runtime-architecture.md`
  - `docs/approved/twin-runtime-reentry-watchdog.md`
  - `docs/approved/twin-universal-host-supervisor.md`
- Risk Focus:
  - 逻辑错误: Route, revision, token, handoff, and terminal-state transitions must remain atomic and deterministic.
  - 行为回归: Existing schema v1 workspaces and the `/twin` launcher paths must remain usable.
  - 安全问题: Provider sandbox flags, CAO bearer transport, redirects, and process termination must fail closed.
  - 运行时: Reentry, bounded watch, concurrent driver acquisition, timeout cleanup, and dirty worktree preservation must remain observable.

## Acceptance Criteria

1. AC-001 (正向): Given one workspace, when Claude, Codex, or Antigravity invokes `twin run --supervisor host/<provider>`, then each route uses the same host protocol and can reach a terminal action without a provider-specific state machine.
2. AC-002 (正向): Given a plan selecting `local_cli`, when Claude, Codex, or Gemini is selected, then twin invokes that installed CLI directly without requiring CAO; CAO remains an optional contract-backed worker route.
3. AC-003 (负向): Given stale revisions, duplicate or cross-workspace tokens, route drift, pending handoff, wrong-run review, or low-level mutation bypass, when a submission is attempted, then twin rejects it without silently changing workspace ownership.
4. AC-004 (运行时): Given worker artifacts in active, quiet, completed, stale, or concurrent states, when twin re-enters or watches the workspace, then it selects the deterministic next action, bounds waiting, fails closed on lock contention, and preserves dirty worktrees.
5. AC-005 (安全): Given local CLI and CAO worker execution, when sandboxing, timeout, bearer transport, redirect, or malformed-response boundaries are exercised, then twin applies provider-specific isolation, terminates the process group on timeout, and rejects unsafe CAO transport.
6. AC-006 (回归): Given schema v1 workspaces and existing `/twin` user paths, when the fixture suite runs, then legacy loading, real launcher behavior, hidden internal commands, and provider-neutral contract generation continue to pass.

## Assertions

- Host routes persist one owner and reject stale, duplicate, wrong-route, wrong-run, and cross-workspace submissions.
- Local Claude, Codex, and Gemini execution records the selected provider; CAO remains optional and contract-isolated.
- Watchdog timeout does not kill workers or mutate state, and concurrent driver acquisition fails closed.
- Provider timeouts terminate child processes; CAO credentials are never sent over unsafe plaintext or redirects.
- The real `twin` launcher and generated Agent contract expose only the approved public and action commands.

## Linked Tests

- AC-001: `scripts/twin/validate.py::_driver_protocol_errors`
- AC-002: `scripts/twin/validate.py::_worker_backend_errors`
- AC-003: `scripts/twin/validate.py::_driver_protocol_errors`
- AC-004: `scripts/twin/validate.py::_runtime_reentry_errors`
- AC-004: `scripts/twin/validate.py::_driver_protocol_errors`
- AC-004: `scripts/twin/validate.py::_worktree_cleanup_errors`
- AC-005: `scripts/twin/validate.py::_worker_backend_errors`
- AC-006: `scripts/twin/validate.py::_driver_protocol_errors`
- Run command: `python3 -m scripts.twin validate --fixtures`

## Evidence

- `scripts/preflight.sh` runs the linked fixture command and the Story-to-test drift check on every PR gate.
- GitHub Agent Contract CI runs the same preflight contract on Python 3.9 and 3.12.

## Status

- [x] Done
