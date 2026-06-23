#!/usr/bin/env bash
# skill-reflect-worker.sh — runs the meta-reflection + optional PR creation.
#
# Invoked detached by skill-reflect.sh (never called directly by Claude Code).
#
# Two phases:
#   1. Agent phase: spawn `claude -p` with READ-ONLY tools + Write-to-staging only.
#      Agent emits markers on stdout:
#        DECISION: skip <reason>
#        OR
#        DECISION: change
#        COMMIT_MSG: <line>
#        PR_TITLE:  <line>
#        PR_BODY:   <multi-line until EOF>
#      Agent must Write the proposed new file content to $STAGING_FILE.
#
#   2. Worker phase: this script — runs all git/gh operations behind hard gates.
#      Agent never touches git/gh, so a hallucinating agent cannot misfire.
#
# Hard gates (any failure → SKIP, never half-done state):
#   - staging file exists, non-empty, differs from original
#   - skill is inside a git repo
#   - repo working tree clean
#   - current branch is main/master (don't pile on user's feature branch)
#   - no existing open PR with the same skill-reflect title (cross-session dedup)
#
# Args: $1 = skill_name  $2 = skill_path  $3 = session_id

set -uo pipefail

SKILL_NAME="${1:?skill_name required}"
SKILL_PATH="${2:?skill_path required}"
SESSION_ID="${3:?session_id required}"

# Defensive: refuse to run inside a recursive spawn even though the hook also guards.
[ "${SKILL_REFLECT_WORKER_RUNNING:-}" = "1" ] && {
  echo "REFUSE: worker recursion detected"
  exit 0
}
export SKILL_REFLECT_WORKER_RUNNING=1

ROOT_DIR="$HOME/.claude/skill-reflections"
SKIP_LOG="$ROOT_DIR/skipped.log"
PR_LOG="$ROOT_DIR/prs.log"
mkdir -p "$ROOT_DIR" 2>/dev/null

# Per-(session, skill) staging dir. Hash for filesystem-safe naming.
STAGING_KEY="$(printf '%s|%s' "$SESSION_ID" "$SKILL_NAME" | shasum | awk '{print $1}' | cut -c1-16)"
STAGING_DIR="/tmp/skill-reflect-staging-$STAGING_KEY"
STAGING_FILE="$STAGING_DIR/proposed"

# Tracks repo state for cleanup. Set as we progress; cleared on success.
TRAP_REPO=""
TRAP_BRANCH_ORIG=""
TRAP_BRANCH_NEW=""

ts() { date +%Y-%m-%dT%H:%M:%S; }

log_skip() {
  printf '%s\t%s\t%s\n' "$(ts)" "$SKILL_NAME" "$1" >> "$SKIP_LOG"
  echo "SKIP: $1"
}

log_pr() {
  printf '%s\t%s\t%s\n' "$(ts)" "$SKILL_NAME" "$1" >> "$PR_LOG"
  echo "PR: $1"
}

cleanup() {
  rm -rf "$STAGING_DIR"
  # Restore repo state only if we mutated it but didn't reach PR-created state.
  if [ -n "$TRAP_REPO" ]; then
    git -C "$TRAP_REPO" checkout -- . 2>/dev/null || true
    if [ -n "$TRAP_BRANCH_ORIG" ]; then
      git -C "$TRAP_REPO" checkout "$TRAP_BRANCH_ORIG" 2>/dev/null || true
    fi
    if [ -n "$TRAP_BRANCH_NEW" ]; then
      git -C "$TRAP_REPO" branch -D "$TRAP_BRANCH_NEW" 2>/dev/null || true
    fi
  fi
}
trap cleanup EXIT

mkdir -p "$STAGING_DIR"

# ---- locate claude + prompt template ----
CLAUDE_BIN="$(command -v claude || true)"
[ -z "$CLAUDE_BIN" ] && { log_skip "claude binary not found"; exit 0; }

DEV_RULES_HOME="${DEV_RULES_HOME:-$HOME/Codes/dev-rules}"
PROMPT_TEMPLATE="$DEV_RULES_HOME/global/hooks/skill-reflect.prompt.md"
[ -f "$PROMPT_TEMPLATE" ] || { log_skip "prompt template missing at $PROMPT_TEMPLATE"; exit 0; }

# ---- build prompt via envsubst ----
# envsubst only substitutes named vars; literal $ in prompt body stays intact.
if ! command -v envsubst >/dev/null 2>&1; then
  log_skip "envsubst not installed (brew install gettext)"
  exit 0
fi

PROMPT="$(SKILL_NAME="$SKILL_NAME" SKILL_PATH="$SKILL_PATH" SESSION_ID="$SESSION_ID" STAGING_FILE="$STAGING_FILE" \
  envsubst '$SKILL_NAME $SKILL_PATH $SESSION_ID $STAGING_FILE' < "$PROMPT_TEMPLATE")"

# ---- Phase 1: agent ----
# Tools strictly limited: no Bash, no Edit, no WebFetch, no nested Skill/Agent.
# Write scoped to staging dir. If the harness ignores path scoping under
# bypassPermissions, the prompt still tells the agent not to write elsewhere,
# AND the worker never propagates anything outside $STAGING_FILE — so a rogue
# Write can at worst clobber some random file but cannot poison the PR.
echo "=== prompt sent to agent (truncated) ==="
printf '%s\n' "$PROMPT" | head -20
echo "..."
echo ""
echo "=== launching claude -p ==="

AGENT_OUTPUT="$("$CLAUDE_BIN" -p "$PROMPT" \
  --allowedTools "Read Glob Grep Write($STAGING_DIR/*)" \
  --max-budget-usd 5 2>&1)" || {
  log_skip "claude -p exit $?"
  exit 0
}

echo "=== agent output ==="
printf '%s\n' "$AGENT_OUTPUT"
echo "=== end agent output ==="

# ---- parse markers ----
DECISION_LINE="$(printf '%s\n' "$AGENT_OUTPUT" | grep -m1 '^DECISION: ' || true)"
if [ -z "$DECISION_LINE" ]; then
  log_skip "agent did not emit DECISION line"
  exit 0
fi

DECISION_BODY="${DECISION_LINE#DECISION: }"

case "$DECISION_BODY" in
  skip*)
    REASON="${DECISION_BODY#skip}"
    REASON="${REASON# }"
    log_skip "agent skipped: ${REASON:-no reason given}"
    exit 0
    ;;
  change)
    : # fall through to apply phase
    ;;
  *)
    log_skip "agent emitted unknown DECISION: $DECISION_BODY"
    exit 0
    ;;
esac

# ---- validate staging file ----
if [ ! -f "$STAGING_FILE" ]; then
  log_skip "decision=change but staging file missing"
  exit 0
fi
if [ ! -s "$STAGING_FILE" ]; then
  log_skip "decision=change but staging file empty"
  exit 0
fi
if cmp -s "$STAGING_FILE" "$SKILL_PATH"; then
  log_skip "staging file identical to original (no real change)"
  exit 0
fi

# ---- git pre-flight ----
REPO_DIR="$(dirname "$SKILL_PATH")"
REPO="$(git -C "$REPO_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
[ -z "$REPO" ] && { log_skip "skill not inside a git repo"; exit 0; }

if [ -n "$(git -C "$REPO" status --porcelain)" ]; then
  log_skip "repo working tree not clean ($REPO)"
  exit 0
fi

CURRENT_BRANCH="$(git -C "$REPO" branch --show-current)"
case "$CURRENT_BRANCH" in
  main|master) ;;
  *)
    log_skip "repo not on main/master (on $CURRENT_BRANCH)"
    exit 0
    ;;
esac

# ---- PR de-dup ----
REPO_REMOTE="$(git -C "$REPO" remote get-url origin 2>/dev/null || true)"
[ -z "$REPO_REMOTE" ] && { log_skip "no origin remote"; exit 0; }

# Normalize SSH (git@host:owner/repo.git) and HTTPS (https://host/owner/repo.git) → owner/repo.
OWNER_REPO="$(printf '%s' "$REPO_REMOTE" \
  | sed -E -e 's#^git@[^:]+:##' -e 's#^https?://[^/]+/##' -e 's#\.git$##')"

if [ -z "$OWNER_REPO" ] || ! printf '%s' "$OWNER_REPO" | grep -qE '^[^/]+/[^/]+$'; then
  log_skip "could not parse owner/repo from remote: $REPO_REMOTE"
  exit 0
fi

EXISTING_PR="$(gh -R "$OWNER_REPO" pr list --state open \
  --search "skill($SKILL_NAME) in:title" --json url --jq '.[0].url // empty' 2>/dev/null || true)"
if [ -n "$EXISTING_PR" ]; then
  log_skip "existing open PR for skill($SKILL_NAME): $EXISTING_PR"
  exit 0
fi

# ---- extract commit/PR text ----
COMMIT_MSG="$(printf '%s\n' "$AGENT_OUTPUT" | grep -m1 '^COMMIT_MSG: ' | sed 's/^COMMIT_MSG: //' || true)"
[ -z "$COMMIT_MSG" ] && COMMIT_MSG="refactor(skill/$SKILL_NAME): meta-reflect updates"

PR_TITLE="$(printf '%s\n' "$AGENT_OUTPUT" | grep -m1 '^PR_TITLE: ' | sed 's/^PR_TITLE: //' || true)"
[ -z "$PR_TITLE" ] && PR_TITLE="skill($SKILL_NAME): meta-reflect updates"

# PR_BODY: everything after the PR_BODY: marker, until EOF.
PR_BODY="$(printf '%s\n' "$AGENT_OUTPUT" | awk '
  /^PR_BODY:/ { flag=1; sub(/^PR_BODY: */, ""); print; next }
  flag { print }
')"
[ -z "$PR_BODY" ] && PR_BODY="Auto-generated by skill-reflect hook (session $SESSION_ID)."

# ---- apply: branch + commit + push + PR ----
NEW_BRANCH="skill-reflect/$SKILL_NAME-$(date +%Y%m%d%H%M%S)"

TRAP_REPO="$REPO"
TRAP_BRANCH_ORIG="$CURRENT_BRANCH"

git -C "$REPO" checkout -b "$NEW_BRANCH" 2>&1 || { log_skip "git checkout -b failed"; exit 0; }
TRAP_BRANCH_NEW="$NEW_BRANCH"

cp "$STAGING_FILE" "$SKILL_PATH" || { log_skip "cp staging→skill failed"; exit 0; }
git -C "$REPO" add -- "$SKILL_PATH" || { log_skip "git add failed"; exit 0; }
git -C "$REPO" commit -m "$COMMIT_MSG" 2>&1 || { log_skip "git commit failed"; exit 0; }

HEAD_SHORT="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || true)"
BASE_REF="$CURRENT_BRANCH"
if git -C "$REPO" rev-parse --verify --quiet "origin/$CURRENT_BRANCH" >/dev/null 2>&1; then
  BASE_REF="origin/$CURRENT_BRANCH"
fi
MERGE_BASE="$(git -C "$REPO" merge-base HEAD "$BASE_REF" 2>/dev/null || true)"
if [ -n "$MERGE_BASE" ]; then
  COMMIT_LIST="$(git -C "$REPO" log --oneline "$MERGE_BASE"..HEAD 2>/dev/null || true)"
else
  COMMIT_LIST="$(git -C "$REPO" log --oneline "$BASE_REF"..HEAD 2>/dev/null || true)"
fi
PR_BODY="$(printf '%s\n\n## 提交\n%s\n\n最新提交：%s\n' "$PR_BODY" "$COMMIT_LIST" "$HEAD_SHORT")"
PR_BODY_FILE="$STAGING_DIR/pr-body.md"
printf '%s\n' "$PR_BODY" > "$PR_BODY_FILE"
if ! python3 "$DEV_RULES_HOME/global/hooks/gh-pr-guard.py" validate-body-file "$PR_BODY_FILE" "$REPO" "$CURRENT_BRANCH" 2>&1; then
  log_skip "generated PR body failed Chinese/freshness validation"
  exit 0
fi

git -C "$REPO" push -u origin "$NEW_BRANCH" 2>&1 || { log_skip "git push failed"; exit 0; }

PR_URL="$(gh -R "$OWNER_REPO" pr create --title "$PR_TITLE" --body-file "$PR_BODY_FILE" 2>&1 || true)"
if ! printf '%s' "$PR_URL" | grep -qE '^https?://'; then
  log_skip "gh pr create failed: $PR_URL (branch pushed at $NEW_BRANCH)"
  exit 0
fi

log_pr "$PR_URL"

# PR created. Return to original branch and skip branch-delete (it's pushed now).
TRAP_BRANCH_NEW=""  # don't delete the pushed branch
git -C "$REPO" checkout "$CURRENT_BRANCH" 2>/dev/null || true
TRAP_REPO=""        # disable repo cleanup

exit 0
