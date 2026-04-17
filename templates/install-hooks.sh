#!/usr/bin/env bash
#
# dev-rules/templates/install-hooks.sh — 安装 git pre-commit hook
#
# 把 scripts/preflight.sh 接到 git pre-commit，让硬约束在 commit 时自动触发。
# 这是 OPC「自动化优先」原则的最后一公里：从「记得跑脚本」→「不可能忘记跑」。
#
# 用法（在项目根目录）：
#   bash dev-rules/templates/install-hooks.sh
#
# 卸载：rm .git/hooks/pre-commit

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK="$REPO_ROOT/.git/hooks/pre-commit"

if [ ! -f "$REPO_ROOT/scripts/preflight.sh" ]; then
    echo "FAIL: scripts/preflight.sh not found. Create it first (see dev-rules/templates/preflight.sh)."
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
# Runs scripts/preflight.sh before every commit. Bypass with --no-verify
# (discouraged — only acceptable for emergency reverts).

REPO_ROOT="$(git rev-parse --show-toplevel)"
exec "$REPO_ROOT/scripts/preflight.sh"
HOOK_EOF
chmod +x "$HOOK"

echo "Installed pre-commit hook → $HOOK"
echo ""
echo "Test with:"
echo "  echo 'test' >> .cursor/rules/safe-shell-commands.mdc"
echo "  git commit -am 'test' --allow-empty   # should fail"
echo "  git checkout .cursor/rules/safe-shell-commands.mdc"
