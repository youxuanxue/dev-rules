#!/usr/bin/env bash
# skill-reflect.sh — PostToolUse hook for the Skill tool.
#
# After a Skill invocation, asynchronously spawn a `claude -p` agent that:
#   1. locates the skill source (only in user-modifiable locations);
#   2. decides if the skill is worth optimizing (strict gate — skip > false-positive);
#   3. if yes, creates a branch + commits + opens a PR to the owning repo.
#
# Dedup: at most once per (session_id, skill_name).
# Recursion guard: the spawned reflection inherits SKILL_REFLECT_RUNNING=1, which
#                  short-circuits this script on any Skill use inside the reflection.
# Async: this script returns in ms; the reflection itself runs detached.

set -uo pipefail

# ---- Recursion guard -------------------------------------------------------
[ "${SKILL_REFLECT_RUNNING:-}" = "1" ] && exit 0

# ---- Parse PostToolUse JSON from stdin -------------------------------------
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

# Sanitize skill_name for filesystem use (defensive — shouldn't have weird chars).
case "$skill_name" in
  */*|*..*|"") exit 0 ;;
esac

# ---- Dedup: max once per (session, skill) ----------------------------------
ROOT_DIR="$HOME/.claude/skill-reflections"
SENTINEL_DIR="$ROOT_DIR/.dedup"
mkdir -p "$SENTINEL_DIR" 2>/dev/null
SENTINEL="$SENTINEL_DIR/${session_id}-${skill_name}"
[ -e "$SENTINEL" ] && exit 0

# ---- Locate user-modifiable skill source -----------------------------------
# Search order:
#   1. project-local cursor skill           ($cwd/.cursor/skills/<name>/SKILL.md)
#   2. user-global cursor skill             (~/.cursor/skills/<name>/SKILL.md)
#   3. dev-rules slash command              (~/Codes/dev-rules/commands/<name>.md)
#   4. project-local Claude Code command    ($cwd/.claude/commands/<name>.md)
# Anything not in these locations (plugin/Anthropic skill) is skipped silently.
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

# Mark sentinel either way — non-modifiable skills shouldn't re-trigger this session.
touch "$SENTINEL"

if [ -z "$SKILL_PATH" ]; then
  exit 0
fi

# ---- Find claude binary ----------------------------------------------------
CLAUDE_BIN="$(command -v claude || true)"
[ -z "$CLAUDE_BIN" ] && exit 0

# ---- Prepare log paths -----------------------------------------------------
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR" 2>/dev/null
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/${TS}-${skill_name}.log"
SKIP_LOG="$ROOT_DIR/skipped.log"
PR_LOG="$ROOT_DIR/prs.log"

# ---- Build reflection prompt -----------------------------------------------
# Use literal heredoc; substitute placeholders via bash parameter expansion
# (safe even when values contain slashes — no sed involved).
PROMPT=$(cat <<'PROMPT_EOF'
你是 skill 元反思 agent，被 PostToolUse hook 异步触发。Headless 模式。

## 上下文
- 刚用过的 skill：__SKILL_NAME__
- skill 源文件：__SKILL_PATH__
- 触发会话 ID：__SESSION_ID__
- skip 日志：__SKIP_LOG__
- PR 日志：__PR_LOG__

## 任务

按顺序执行：

1. **读源文件**：Read __SKILL_PATH__。理解 skill 当前的设计意图、边界、措辞。

2. **看最近演进**（仅作背景，不据此推断"必须改"）：
   - `REPO_DIR="$(dirname __SKILL_PATH__)"`
   - `REPO="$(git -C "$REPO_DIR" rev-parse --show-toplevel)"`
   - `git -C "$REPO" log --oneline -5 -- "__SKILL_PATH__"`

3. **从上帝视角评估** skill 是否值得改：
   - 值得改的信号：措辞模糊导致触发不稳、缺失常见反模式提示、输出格式可改让下游消费更省 token、有明显遗漏的边界情况。
   - 不该改的信号：仅文笔/排版洁癖、个人偏好无凭据、猜测性"防御性"补充、改动 < 5 行且收益不明。
   - 默认偏保守：**当犹豫就 skip**。

4. **决策**：
   - **不改**：往 __SKIP_LOG__ 追加一行 `<ISO 时间戳>\t__SKILL_NAME__\t<一句话理由>`，结束（输出 "SKIP: <理由>"）。
   - **改**：进入步骤 5。

5. **改 + PR 流程**（任一步失败 → 立即放弃 + 写 skip 日志）：
   a. `REPO="$(git -C "$(dirname __SKILL_PATH__)" rev-parse --show-toplevel)"`
   b. `git -C "$REPO" status --porcelain` 必须为空。非空 → 放弃（不污染未提交工作）。
   c. 当前分支必须是 main/master。否则 → 放弃（不要在用户的 feature 分支上叠）。
   d. `BRANCH="skill-reflect/__SKILL_NAME__-$(date +%Y%m%d%H%M%S)"`
   e. `git -C "$REPO" checkout -b "$BRANCH"`
   f. 用 Edit 工具最小化修改 __SKILL_PATH__（**只允许改这一个文件**）。
   g. `git -C "$REPO" add` 该文件 + `git -C "$REPO" commit -m "<一句话>"`
   h. `git -C "$REPO" push -u origin "$BRANCH"`
   i. `gh -R <owner/repo> pr create --title "skill(__SKILL_NAME__): <一句话>" --body "<触发上下文 + 识别到的问题 + 改动方案 + 为何这次改值得>"`
   j. PR URL 追加到 __PR_LOG__ 一行：`<ISO 时间戳>\t__SKILL_NAME__\t<PR URL>`。

## 严格约束
- **只允许修改 __SKILL_PATH__ 一个文件**。不动任何其他文件。
- 禁止 force push、reset --hard、amend、rebase、删除文件。
- 禁止在 main/master 上直接 commit（必须新建分支）。
- working tree 不干净 → 立即放弃。
- 当前分支不是 main/master → 立即放弃。
- 不要尝试调任何 Skill 工具（防递归）。
- 任何不确定 → 写 skip 日志放弃，**不要硬上**。

## 输出
一句话报告：`SKIP: <reason>` 或 `PR: <url>`。
PROMPT_EOF
)

# Substitute placeholders (bash // — handles slashes literally).
PROMPT="${PROMPT//__SKILL_NAME__/$skill_name}"
PROMPT="${PROMPT//__SKILL_PATH__/$SKILL_PATH}"
PROMPT="${PROMPT//__SESSION_ID__/$session_id}"
PROMPT="${PROMPT//__SKIP_LOG__/$SKIP_LOG}"
PROMPT="${PROMPT//__PR_LOG__/$PR_LOG}"

# ---- Spawn detached reflection ---------------------------------------------
# - SKILL_REFLECT_RUNNING=1: recursion guard for nested Skill use.
# - nohup + & + disown + </dev/null: fully detach so the parent session moves on.
# - --max-budget-usd 5: cap cost per reflection.
# - --allowedTools: restrict surface (no WebFetch, no Agent, no Skill).
SKILL_REFLECT_RUNNING=1 nohup \
  "$CLAUDE_BIN" -p "$PROMPT" \
  --allowedTools "Read,Edit,Bash,Glob,Grep" \
  --max-budget-usd 5 \
  >"$LOG" 2>&1 </dev/null &
disown

exit 0
