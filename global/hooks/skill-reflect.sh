#!/usr/bin/env bash
# skill-reflect.sh — PostToolUse hook for the Skill tool.
#
# Thin gate that spawns skill-reflect-worker.sh detached in the background.
# Returns in milliseconds so the parent Claude session isn't blocked.
#
# Gates (each → exit 0 silently):
#   - SKILL_REFLECT_RUNNING=1 (we're inside a reflection's own subprocess)
#   - stdin empty / tool_name != Skill / no skill_name / no session_id
#   - sentinel exists (same session+skill already triggered this run)
#   - skill source not in user-modifiable location (Anthropic plugin etc.)
#   - worker script missing / not executable
#
# Housekeeping: at each fire, prune sentinels and logs older than 7 days.

set -uo pipefail

# Recursion guard — also propagated to spawned worker as SKILL_REFLECT_WORKER_RUNNING.
[ "${SKILL_REFLECT_RUNNING:-}" = "1" ] && exit 0

INPUT="$(cat)"
[ -z "$INPUT" ] && exit 0

JQ="/usr/bin/jq"
command -v "$JQ" >/dev/null 2>&1 || JQ="jq"

tool_name="$(printf '%s' "$INPUT" | "$JQ" -r '.tool_name // ""' 2>/dev/null)"
[ "$tool_name" = "Skill" ] || exit 0

skill_name="$(printf '%s' "$INPUT" | "$JQ" -r '.tool_input.skill // ""' 2>/dev/null)"
session_id="$(printf '%s' "$INPUT" | "$JQ" -r '.session_id // ""' 2>/dev/null)"
cwd="$(printf '%s' "$INPUT" | "$JQ" -r '.cwd // ""' 2>/dev/null)"
[ -z "$skill_name" ] && exit 0
[ -z "$session_id" ] && exit 0

# Defensive sanitization: refuse path-traversal-y values in either field.
case "$skill_name" in */*|*..*|"") exit 0 ;; esac
case "$session_id" in */*|*..*|"") exit 0 ;; esac

ROOT_DIR="$HOME/.claude/skill-reflections"
SENTINEL_DIR="$ROOT_DIR/.dedup"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$SENTINEL_DIR" "$LOG_DIR" 2>/dev/null

# Housekeeping: 7-day retention. Cheap; silent on errors.
find "$SENTINEL_DIR" -type f -mtime +7 -delete 2>/dev/null || true
find "$LOG_DIR" -type f -mtime +7 -delete 2>/dev/null || true

SENTINEL="$SENTINEL_DIR/${session_id}-${skill_name}"
[ -e "$SENTINEL" ] && exit 0

# Locate user-modifiable skill source. Search order:
#   1. project-local cursor skill
#   2. user-global cursor skill
#   3. dev-rules slash command (twin lives here; xj-review is now a .cursor/skill)
#   4. project-local Claude Code command
DEV_RULES_HOME="${DEV_RULES_HOME:-$HOME/Codes/dev-rules}"
SKILL_PATH=""
for cand in \
  "$cwd/.cursor/skills/$skill_name/SKILL.md" \
  "$HOME/.cursor/skills/$skill_name/SKILL.md" \
  "$DEV_RULES_HOME/commands/$skill_name.md" \
  "$cwd/.claude/commands/$skill_name.md"; do
  if [ -f "$cand" ]; then
    SKILL_PATH="$cand"
    break
  fi
done

# Sentinel either way — Anthropic plugin skills shouldn't keep firing.
touch "$SENTINEL"

[ -z "$SKILL_PATH" ] && exit 0

WORKER="$DEV_RULES_HOME/global/hooks/skill-reflect-worker.sh"
[ -x "$WORKER" ] || exit 0

TS="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/${TS}-${skill_name}.log"

# Spawn worker fully detached. SKILL_REFLECT_RUNNING shorts this hook if the
# worker's claude -p call ever triggers another Skill (it shouldn't — agent
# has no Skill tool — but defense in depth).
SKILL_REFLECT_RUNNING=1 nohup \
  "$WORKER" "$skill_name" "$SKILL_PATH" "$session_id" \
  >"$LOG" 2>&1 </dev/null &
disown

exit 0
