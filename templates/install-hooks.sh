#!/usr/bin/env bash
#
# dev-rules/templates/install-hooks.sh — 安装 git pre-commit hook
#
# 把 preflight 脚本接到 git pre-commit，让硬约束在 commit 时自动触发。
# 这是 OPC「自动化优先」原则的最后一公里：从「记得跑脚本」→「不可能忘记跑」。
#
# 解析顺序（fallback chain）：
#   1. $REPO_ROOT/scripts/preflight.sh        ← 项目级 wrapper（有项目特定检查时使用）
#   2. $REPO_ROOT/dev-rules/templates/preflight.sh  ← dev-rules 模板（仅 8 段通用检查）
#
# 用法（在项目根目录）：
#   bash dev-rules/templates/install-hooks.sh
#
# 卸载：rm .git/hooks/pre-commit

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK="$REPO_ROOT/.git/hooks/pre-commit"

PROJECT_PREFLIGHT="$REPO_ROOT/scripts/preflight.sh"
TEMPLATE_PREFLIGHT="$REPO_ROOT/dev-rules/templates/preflight.sh"

if [ -f "$PROJECT_PREFLIGHT" ]; then
    echo "Found project-level preflight: $PROJECT_PREFLIGHT"
    PREFLIGHT_TARGET="project"
elif [ -f "$TEMPLATE_PREFLIGHT" ]; then
    echo "No scripts/preflight.sh found — falling back to dev-rules template (8 sections only)."
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
# dev-rules/templates/preflight.sh (generic 8-section template). The hook
# resolves at runtime, so adding/removing scripts/preflight.sh later
# requires no re-install.

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
echo ""
echo "Test with:"
echo "  git commit --allow-empty -m 'test'   # should run preflight"
