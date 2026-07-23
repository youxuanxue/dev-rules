---
approved_by: user-chat-2026-07-23
status: approved
risk_level: high
---

# twin universal host supervisor

## Intent

Deliver one universal twin host-supervisor path. Codex must be able to supervise the same workspace and worker backends as Claude without copying the host workflow or twin state machine into a second prompt surface.

## Approved architecture

- `global/bin/twin` is the provider-neutral executable and delegates to `scripts.twin`.
- `twin run <workspace> --supervisor host/<provider>` drives deterministic actions until host judgment, a bounded wait, a human gate, or a terminal state is reached.
- The CLI action payload is self-describing. It includes bounded context, the expected decision shape, state revision, one-time action token, and the exact deterministic submission command.
- `agent-skills/twin/SKILL.md` is the single host-workflow owner. Claude `/twin`, Codex `$twin`, and Antigravity consume that same skill through the symlinks managed by `sync.sh`.
- The generated Agent contract owns only live CLI and schema inventories; it does not copy a host-specific command surface.
- Host providers initially include `claude`, `codex`, and `antigravity`. The current host performs only instruction and review judgment; Python owns validation and artifact mutation.
- Supervisor route is persisted. A different route cannot silently submit a decision. Legacy workspaces bind lazily on their first universal `twin run`.
- `twin handoff <workspace> --supervisor host/<provider>` is the only route-transfer path. It requires no pending action, holds the workspace driver lock, advances the state revision, and writes an audit event. Same-route handoff is idempotent; the old route and tokens remain invalid.
- Route-bound workspaces reject low-level worker/review mutation outside the driver submission protocol. Compatibility commands remain callable for unbound legacy/test flows but are hidden from public help and the generated Agent contract.
- State revisions and action tokens reject stale, duplicate, wrong-action, wrong-run, and cross-workspace submissions.
- Existing worker routing remains unchanged: `claude_headless`, direct `local_cli`, or optional CAO.

## Compatibility

- Existing schema version 1 workspaces remain readable.
- New driver fields are additive and initialized lazily.
- Existing `python3 -m scripts.twin` commands remain available as internal and compatibility surfaces.
- Existing `/twin <workspace>`, `/twin status`, and `/twin respond` user paths continue to work through the shared `twin` skill.

## Non-goals

- No unattended CAO supervisor in this change.
- No Web UI, daemon, Agent swarm, or cross-machine scheduler.
- No duplicate Codex-only state machine or supervisor runbook.
- No automatic bypass of `needs_human`, high-risk approval, or merge authorization.

## Validation

- Python 3.9 and 3.12 fixture suites.
- Real launcher and generated Agent contract checks.
- Shared-skill loading and stale Claude command cleanup checks.
- Codex host protocol smoke over an isolated fixture workspace.
- Negative tests for stale revision, duplicate token, route drift, wrong run, and legacy workspace binding.
- Negative tests for pending handoff, old-route reuse, low-level mutation bypass, and internal command exposure.
- Full `scripts/preflight.sh` and high-risk full-conformance review.
