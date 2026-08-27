#!/usr/bin/env bash
#
# dev-rules/templates/install-hooks.sh — 安装 git hooks
#
# 把 preflight 脚本接到 git pre-commit，让硬约束在 commit 时自动触发。
# 这是「确定性自动化运营和运维」「自动化优先」原则的最后一公里：从「记得跑脚本」→「不可能忘记跑」。
#
# 解析顺序（fallback chain）：
#   1. $REPO_ROOT/scripts/preflight.sh        ← 项目级 wrapper（有项目特定检查时使用）
#   2. $REPO_ROOT/dev-rules/templates/preflight.sh  ← dev-rules 模板（通用检查段，见模板文件头）
#
# 固定 hook：
#   commit-msg — 高风险审批锚点的硬拦截点。
#   pre-commit 结构上读不到待提交 message（COMMIT_EDITMSG 此时还是上一条），
#   commit-msg 是本地唯一同时知道 staged paths + message 的阶段，
#   token 缺失在这里被硬拦截而不是等到 CI。
#
# 可选 hook：
#   pre-push — 当 $REPO_ROOT/scripts/pre-push-web-surface.sh 存在时自动安装
#
# 用法（在项目根目录）：
#   bash dev-rules/templates/install-hooks.sh
#
# 卸载：
#   rm "$(git rev-parse --git-path hooks/pre-commit)" \
#      "$(git rev-parse --git-path hooks/commit-msg)" \
#      "$(git rev-parse --git-path hooks/pre-push)"

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"

git_hook_path() {
    local name="$1"
    local path
    path="$(git -C "$REPO_ROOT" rev-parse --git-path "hooks/$name")"
    case "$path" in
        /*) printf '%s\n' "$path" ;;
        *) printf '%s\n' "$REPO_ROOT/$path" ;;
    esac
}

HOOK="$(git_hook_path pre-commit)"
mkdir -p "$(dirname "$HOOK")"

PROJECT_PREFLIGHT="$REPO_ROOT/scripts/preflight.sh"
TEMPLATE_PREFLIGHT="$REPO_ROOT/dev-rules/templates/preflight.sh"

if [ -f "$PROJECT_PREFLIGHT" ]; then
    echo "Found project-level preflight: $PROJECT_PREFLIGHT"
    PREFLIGHT_TARGET="project"
elif [ -f "$TEMPLATE_PREFLIGHT" ]; then
    echo "No scripts/preflight.sh found — falling back to dev-rules template (generic sections only)."
    echo "  → $TEMPLATE_PREFLIGHT"
    PREFLIGHT_TARGET="template"
else
    echo "FAIL: neither $PROJECT_PREFLIGHT nor $TEMPLATE_PREFLIGHT exists."
    echo "      Add dev-rules as a submodule first, or copy templates/preflight.sh."
    exit 1
fi

if [ -f "$HOOK" ] && ! grep -q "preflight.sh" "$HOOK"; then
    echo "FAIL: $HOOK already exists and does not call preflight.sh."
    echo "      Inspect it manually before overwriting."
    exit 1
fi

cat > "$HOOK" <<'HOOK_EOF'
#!/usr/bin/env bash
#
# Auto-installed by dev-rules/templates/install-hooks.sh
# Runs the project preflight before every commit. Bypass with --no-verify
# (discouraged — only acceptable for emergency reverts).
#
# Resolution: prefer scripts/preflight.sh (project wrapper), fall back to
# dev-rules/templates/preflight.sh (generic template). The hook resolves at
# runtime, so adding/removing scripts/preflight.sh later requires no re-install.

REPO_ROOT="$(git rev-parse --show-toplevel)"
if [ -x "$REPO_ROOT/scripts/preflight.sh" ]; then
    exec "$REPO_ROOT/scripts/preflight.sh"
elif [ -x "$REPO_ROOT/dev-rules/templates/preflight.sh" ]; then
    exec "$REPO_ROOT/dev-rules/templates/preflight.sh"
else
    echo "pre-commit hook: no preflight script found, allowing commit (degraded mode)" >&2
    exit 0
fi
HOOK_EOF
chmod +x "$HOOK"

echo "Installed pre-commit hook → $HOOK"
echo "  active target: $PREFLIGHT_TARGET preflight (resolved at runtime)"

# --- commit-msg hook (token gate: high-risk approval anchor) ---
COMMIT_MSG_HOOK="$(git_hook_path commit-msg)"

if [ -f "$COMMIT_MSG_HOOK" ] && ! grep -q "check_high_risk_anchor" "$COMMIT_MSG_HOOK"; then
    echo ""
    echo "WARN: $COMMIT_MSG_HOOK already exists and is not the dev-rules token-gate hook."
    echo "      Skipping commit-msg installation — inspect it manually."
else
    cat > "$COMMIT_MSG_HOOK" <<'HOOK_EOF'
#!/usr/bin/env bash
#
# Auto-installed by dev-rules/templates/install-hooks.sh
# Hard gate for the high-risk approval anchor: commit-msg is the only local
# stage where BOTH staged paths and the pending commit message ($1) are known.
# Resolution order covers consumer projects (dev-rules/ submodule) and the
# dev-rules source repo (scripts/).

REPO_ROOT="$(git rev-parse --show-toplevel)"
PYTHON_BIN="$(command -v python3 2>/dev/null || command -v python 2>/dev/null)"
if [ -z "$PYTHON_BIN" ]; then
    echo "commit-msg hook: no python interpreter found, allowing commit (degraded mode)" >&2
    exit 0
fi
CHECK=""
for candidate in \
    "$REPO_ROOT/dev-rules/scripts/check_high_risk_anchor.py" \
    "$REPO_ROOT/scripts/check_high_risk_anchor.py"; do
    if [ -f "$candidate" ]; then CHECK="$candidate"; break; fi
done
[ -n "$CHECK" ] || exit 0
exec "$PYTHON_BIN" "$CHECK" --base "${PREFLIGHT_BASE:-origin/main}" --commit-msg-file "$1"
HOOK_EOF
    chmod +x "$COMMIT_MSG_HOOK"
    echo "Installed commit-msg hook → $COMMIT_MSG_HOOK (token-gate hard stop: high-risk approval anchor)"
fi

# --- pre-push hook (optional) ---
PRE_PUSH_SCRIPT="$REPO_ROOT/scripts/pre-push-web-surface.sh"
PRE_PUSH_HOOK="$(git_hook_path pre-push)"

if [ -f "$PRE_PUSH_SCRIPT" ]; then
    if [ -f "$PRE_PUSH_HOOK" ] && ! grep -q "pre-push-web-surface" "$PRE_PUSH_HOOK"; then
        echo ""
        echo "WARN: $PRE_PUSH_HOOK already exists and is not the web-surface hook."
        echo "      Skipping pre-push installation — inspect it manually."
    else
        ln -sf "$PRE_PUSH_SCRIPT" "$PRE_PUSH_HOOK"
        echo "Installed pre-push hook → $PRE_PUSH_HOOK (symlink to scripts/pre-push-web-surface.sh)"
    fi
fi

echo ""
echo "Test with:"
echo "  git commit --allow-empty -m 'test'   # should run preflight"
