#!/usr/bin/env bash
# worktree-bootstrap.sh — make a freshly-created git worktree turnkey.
#
# A new `git worktree add` (or the Claude Code EnterWorktree primitive, which
# creates under `.claude/worktrees/<name>/`) starts WITHOUT submodules
# initialised and WITHOUT any project-specific sibling/symlink wiring. That
# friction is why people skip worktrees on "small" tasks and end up sharing the
# primary checkout's single mutable HEAD/index — the exact setup that lets a
# parallel agent's `git checkout` land your commits on the wrong branch
# (worktree-per-agent is the only real isolation; this script makes it free).
#
# This is the GENERIC layer (lives in dev-rules, distributed to every consumer
# project). It does two things, idempotently:
#   1. initialise the `dev-rules` submodule inside the worktree, because
#      preflight / git hooks delegate to dev-rules/templates/* and fail on a
#      worktree where the submodule tree is empty;
#   2. run the project's own `scripts/worktree-bootstrap-hook.sh <wt>` if
#      present, for project-specific wiring that the generic layer cannot know
#      about (e.g. sub2api's `../../new-api` sibling symlink for the go.mod
#      replace directive — broken by deep `.claude/worktrees/` nesting).
#
# Idempotent: safe to re-run on an already-bootstrapped worktree.
#
# This is not the public entry point for interactive agents. For normal
# create/switch/remove workflows use $git-worktree-submodule, which also
# enforces shared submodule storage and session-workdir verification.
#
# Usage: worktree-bootstrap.sh <worktree_dir>
set -euo pipefail

WT="${1:?usage: worktree-bootstrap.sh <worktree_dir>}"
if [ ! -d "$WT" ]; then
  echo "[worktree-bootstrap] ERROR: not a directory: $WT" >&2
  exit 2
fi
WT="$(cd "$WT" && pwd)"

# 1. dev-rules submodule — only when the project actually vendors it. grep the
#    worktree's own .gitmodules so this stays a no-op for projects without it.
if [ -f "$WT/.gitmodules" ] && grep -qE '^[[:space:]]*path[[:space:]]*=[[:space:]]*dev-rules[[:space:]]*$' "$WT/.gitmodules"; then
  git -C "$WT" submodule update --init --quiet dev-rules \
    || echo "[worktree-bootstrap] WARN: dev-rules submodule init failed (continuing)" >&2
fi

# 2. project-specific hook (optional). Receives the worktree dir as $1.
HOOK="$WT/scripts/worktree-bootstrap-hook.sh"
if [ -f "$HOOK" ]; then
  bash "$HOOK" "$WT"
fi

echo "[worktree-bootstrap] $WT ready"
