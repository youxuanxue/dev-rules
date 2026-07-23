---
approved_by: user-chat-2026-07-20
status: approved
risk_level: high
---

# twin team runtime architecture

## Intent

Build a real Agent team by separating governance, execution, capabilities, and verification without exposing a mandatory multi-stage process to users.

## Approved architecture

- `twin` owns goals, bounded plans, supervisor review, artifact state, and human gates.
- `wtree.py` is the only worktree implementation. `wts` remains the human wrapper and `git-worktree-submodule` remains the Agent contract.
- Worker execution is selected through a backend interface. Claude headless remains the default; `local_cli` directly runs installed Claude/Codex/Gemini CLIs, while CAO supplies remote, profile-managed and long-tail multi-provider execution through `POST /terminals/run-step`.
- Claude Dynamic Workflow is an optional read-only research accelerator. It produces a sourced `research.yaml`; the twin supervisor remains responsible for the final `goal.yaml` and `plan.yaml`.
- Skills and MCP tools provide specialist capabilities. Tests, preflight, and independent review remain the evidence-based verification layer.

## Safety decisions

- Worktree creation uses the current approved `HEAD`, creates a stable workspace branch, and fails closed. It never silently falls back to the shared checkout.
- Automatic cleanup preserves a worktree with unsaved business changes.
- CAO is consumed through its HTTP contract, not imported as in-process implementation code. A pinned submodule may be added later for bootstrap reproducibility but is not the runtime boundary.
- CAO worker turns are fresh and request `teardown=true`; cross-turn truth lives in twin artifacts.
- A local CAO server is not required for the first-party local CLI path. Provider availability is checked read-only by `python3 -m scripts.twin doctor --json`; unsupported provider budget semantics fail closed.
- Local Codex turns always use `workspace-write` with `approval_policy=never`, including resume; local Gemini combines its OS sandbox with `approval-mode=yolo`. Provider timeouts terminate the whole spawned process group before twin records the turn as finished.
- CAO bearer authentication is allowed over loopback HTTP or HTTPS; it fails closed on non-loopback plaintext HTTP and never follows HTTP redirects.
- Terminal worktree cleanup runs after terminal state is persisted and records removed, preserved, or failed outcomes in workspace evidence.
- Dynamic Workflow may not edit code, branches, commits, external systems, or final planning artifacts during research.

## User experience

The default remains `/twin "<goal>"`. The supervisor invokes research only for materially ambiguous or cross-repository work. Explicit `/twin research` and `/twin plan` entry points are available without making them mandatory stages.

## Validation

- `python3 -m scripts.twin validate --fixtures`
- `python3 scripts/twin/worktree.py`
- `./scripts/preflight.sh`
