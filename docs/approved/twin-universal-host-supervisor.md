---
approved_by: user-chat-2026-07-22
status: approved
risk_level: high
---

# twin universal host supervisor

## Intent

Deliver the universal twin host-supervisor path end to end in PR #88. Codex must be able to supervise the same workspace and worker backends as Claude without copying the twin state machine into a second repository or prompt surface.

## Approved architecture

- `global/bin/twin` is the provider-neutral executable and delegates to `scripts.twin`.
- `twin run <workspace> --supervisor host/<provider>` drives deterministic actions until host judgment, a bounded wait, a human gate, or a terminal state is reached.
- The CLI action payload is self-describing. It includes bounded context, the expected decision shape, state revision, one-time action token, and the exact deterministic submission command.
- Claude `/twin`, Codex, Antigravity, and shell users consume the same CLI and generated Agent contract. PR #88 does not depend on a second `agent-skills` PR.
- Host providers initially include `claude`, `codex`, and `antigravity`. The current host performs only instruction and review judgment; Python owns validation and artifact mutation.
- Supervisor route is persisted. A different route cannot silently submit a decision. Legacy workspaces bind lazily on their first universal `twin run`.
- State revisions and action tokens reject stale, duplicate, wrong-action, wrong-run, and cross-workspace submissions.
- Existing worker routing remains unchanged: `claude_headless`, direct `local_cli`, or optional CAO.

## Compatibility

- Existing schema version 1 workspaces remain readable.
- New driver fields are additive and initialized lazily.
- Existing `python3 -m scripts.twin` commands remain available as internal and compatibility surfaces.
- Existing `/twin <workspace>`, `/twin status`, and `/twin respond` user paths continue to work through the thin Claude adapter.

## Non-goals

- No unattended CAO supervisor in this change.
- No Web UI, daemon, Agent swarm, or cross-machine scheduler.
- No duplicate Codex-only state machine or supervisor runbook.
- No automatic bypass of `needs_human`, high-risk approval, or merge authorization.

## Validation

- Python 3.9 and 3.12 fixture suites.
- Real launcher and generated Agent contract checks.
- Codex host protocol smoke over an isolated fixture workspace.
- Negative tests for stale revision, duplicate token, route drift, wrong run, and legacy workspace binding.
- Full `scripts/preflight.sh` and high-risk full-conformance review.
