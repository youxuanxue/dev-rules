#!/usr/bin/env bash
#
# preflight.sh — 项目级提交前/CI 强约束门禁（模板）
#
# 把本文件复制到 项目/scripts/preflight.sh，按需启用各检查段。
# 与 dev-rules/rules/* 一一对应：每条软规则都对应一个机械检查。
#
# 用法：
#   ./scripts/preflight.sh           # 默认运行所有启用的检查
#   ./scripts/preflight.sh --fix     # 允许部分检查自动修复（如 sync）
#
# 退出码：0 = 全部通过；非 0 = 至少一项失败（CI 应当 fail）
#
# 推荐接入点：
#   1. .git/hooks/pre-commit  （本地拦截）
#   2. .github/workflows/preflight.yml （PR 阻断）
#   3. agent 自检步骤（详见 product-dev.mdc 的「完成自检」节）

set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

FIX_MODE=0
[ "${1:-}" = "--fix" ] && FIX_MODE=1

errors=0
section() { echo ""; echo "=== $* ==="; }
fail()    { echo "  FAIL: $*"; errors=$((errors + 1)); }
ok()      { echo "  ok: $*"; }
skip()    { echo "  skip: $*"; }

# ---- 检查 1: 分支命名 ----（对应 product-dev.mdc 分支命名规范）
section "branch naming (prototype/|feature/|fix/|chore/|main|master)"
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
case "$branch" in
    main|master|prototype/*|feature/*|fix/*|chore/*|docs/*|HEAD)
        ok "branch '$branch'"
        ;;
    *)
        fail "branch '$branch' does not match required prefix"
        ;;
esac

# ---- 检查 2: dev-rules submodule-first 提交顺序 ----（对应 dev-rules-convention.mdc）
section "dev-rules submodule pointer is reachable on remote"
if [ -f .gitmodules ] && grep -q "dev-rules" .gitmodules; then
    sub_sha="$(git submodule status dev-rules | awk '{print $1}' | sed 's/^[+-]//')"
    if (cd dev-rules && git cat-file -e "$sub_sha" 2>/dev/null); then
        ok "submodule SHA $sub_sha exists locally in dev-rules"
        # remote check (warn-only, may fail if offline)
        if (cd dev-rules && git fetch --quiet origin 2>/dev/null) && \
           (cd dev-rules && git merge-base --is-ancestor "$sub_sha" origin/main 2>/dev/null); then
            ok "submodule SHA is reachable on dev-rules origin/main"
        else
            echo "  warn: cannot verify submodule SHA on remote (offline or not pushed yet)"
        fi
    else
        fail "submodule SHA $sub_sha not found in dev-rules — submodule was not committed first"
    fi
else
    skip "dev-rules submodule not configured"
fi

# ---- 检查 3: dev-rules drift ----（对应 sync.sh --check）
section "dev-rules sync drift"
if [ -x dev-rules/sync.sh ]; then
    if [ "$FIX_MODE" -eq 1 ]; then
        dev-rules/sync.sh --local && ok "synced from submodule"
    else
        if dev-rules/sync.sh --check > /tmp/preflight-sync.log 2>&1; then
            ok "no drift between .cursor/rules/ and submodule"
        else
            cat /tmp/preflight-sync.log | sed 's/^/    /'
            fail ".cursor/rules/ has drifted from submodule (re-run with --fix)"
        fi
    fi
else
    skip "dev-rules/sync.sh not available"
fi

# ---- 检查 4: API/CLI/MCP 契约不漂移 ----（对应 agent-contract-enforcement.mdc）
section "agent contract drift"
if [ -f scripts/export_agent_contract.py ]; then
    if python scripts/export_agent_contract.py --check > /tmp/preflight-contract.log 2>&1; then
        ok "contract docs in sync with code"
    else
        cat /tmp/preflight-contract.log | sed 's/^/    /'
        fail "contract docs have drifted (regenerate via 'python scripts/export_agent_contract.py')"
    fi
else
    skip "scripts/export_agent_contract.py not present (create it per agent-contract-enforcement.mdc)"
fi

# ---- 检查 5: User Story ↔ Test 漂移 ----（对应 test-philosophy.mdc）
section "user story / test alignment"
if [ -f .testing/user-stories/verify_quality.py ]; then
    if python .testing/user-stories/verify_quality.py > /tmp/preflight-stories.log 2>&1; then
        ok "stories aligned with tests"
    else
        cat /tmp/preflight-stories.log | sed 's/^/    /'
        fail "story quality / alignment check failed"
    fi
else
    skip ".testing/user-stories/verify_quality.py not present"
fi

# ---- 检查 6: docs/approved/ 不在非 GATE PR 中被修改 ----（对应 product-dev.mdc 阶段 2）
section "docs/approved/ change discipline"
if [ -d docs/approved ]; then
    base="${PREFLIGHT_BASE:-origin/main}"
    if git rev-parse --verify "$base" >/dev/null 2>&1; then
        approved_changed="$(git diff --name-only "$base"...HEAD -- docs/approved/ 2>/dev/null || true)"
        if [ -n "$approved_changed" ]; then
            case "$branch" in
                prototype/*)
                    ok "docs/approved/ modified on prototype branch (allowed)"
                    ;;
                *)
                    echo "  warn: docs/approved/ modified outside prototype/* branch:"
                    echo "$approved_changed" | sed 's/^/    - /'
                    echo "  warn: PR reviewer should confirm this is an intentional approval revision"
                    ;;
            esac
        else
            ok "docs/approved/ unchanged in this branch"
        fi
    else
        skip "no '$base' to diff against"
    fi
else
    skip "docs/approved/ directory not present"
fi

# ---- 检查 7: 待审批产物不应进入 main ----（approved_by: pending 元数据）
section "no docs/approved/ files left as 'approved_by: pending' on main"
if [ -d docs/approved ] && [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
    pending=$(grep -lE '^approved_by:\s*pending\s*$' docs/approved/*.md 2>/dev/null || true)
    if [ -n "$pending" ]; then
        echo "$pending" | sed 's/^/    - /'
        fail "files with approved_by: pending must not land on $branch"
    else
        ok "all approved/* files have a real approver"
    fi
else
    skip "not on main/master, or no docs/approved/"
fi

echo ""
if [ $errors -eq 0 ]; then
    echo "=== preflight: PASS ==="
    exit 0
else
    echo "=== preflight: FAIL ($errors check(s) failed) ==="
    exit 1
fi
