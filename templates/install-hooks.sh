#!/usr/bin/env bash
#
# dev-rules/templates/install-hooks.sh — 安装 git hooks
#
# 把 preflight 脚本接到 git pre-commit，让硬约束在 commit 时自动触发。
# 这是 OPC「自动化优先」原则的最后一公里：从「记得跑脚本」→「不可能忘记跑」。
#
# 解析顺序（fallback chain）：
#   1. $REPO_ROOT/scripts/preflight.sh        ← 项目级 wrapper（有项目特定检查时使用）
#   2. $REPO_ROOT/dev-rules/templates/preflight.sh  ← dev-rules 模板（通用检查段，见模板文件头）
#
# 可选 hook：
#   pre-push — 当 $REPO_ROOT/scripts/pre-push-web-surface.sh 存在时自动安装
#
# 用法（在项目根目录）：
#   bash dev-rules/templates/install-hooks.sh
#
# 卸载：rm .git/hooks/pre-commit .git/hooks/pre-push

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK="$REPO_ROOT/.git/hooks/pre-commit"

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

# --- pre-push hook (optional) ---
PRE_PUSH_SCRIPT="$REPO_ROOT/scripts/pre-push-web-surface.sh"
PRE_PUSH_HOOK="$REPO_ROOT/.git/hooks/pre-push"

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
