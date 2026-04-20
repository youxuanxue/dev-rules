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

# Resolve project root robustly so the script works whether invoked:
#   - directly as $project/scripts/preflight.sh
#   - via a wrapper that exec's $project/dev-rules/templates/preflight.sh
#   - from any cwd
# Strategy: prefer git toplevel of the current directory (caller's cwd),
# fall back to PREFLIGHT_REPO_ROOT env var, finally to script-relative path.
if [ -n "${PREFLIGHT_REPO_ROOT:-}" ] && [ -d "$PREFLIGHT_REPO_ROOT" ]; then
    REPO_ROOT="$PREFLIGHT_REPO_ROOT"
elif git_top="$(git rev-parse --show-superproject-working-tree 2>/dev/null)" && [ -n "$git_top" ]; then
    REPO_ROOT="$git_top"
elif git_top="$(git rev-parse --show-toplevel 2>/dev/null)" && [ -n "$git_top" ]; then
    REPO_ROOT="$git_top"
else
    REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi
cd "$REPO_ROOT"
echo "preflight: repo root = $REPO_ROOT"

FIX_MODE=0
[ "${1:-}" = "--fix" ] && FIX_MODE=1

# Resolve a usable Python interpreter (some macOS / minimal Linux installs only
# have python3, not python). Sections 4 + 5 use $PYTHON_BIN instead of bare
# `python` to avoid `command not found` failures.
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)}"

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
    if "$PYTHON_BIN" scripts/export_agent_contract.py --check > /tmp/preflight-contract.log 2>&1; then
        ok "contract docs in sync with code"
    else
        cat /tmp/preflight-contract.log | sed 's/^/    /'
        fail "contract docs have drifted (regenerate via '$PYTHON_BIN scripts/export_agent_contract.py')"
    fi
else
    skip "scripts/export_agent_contract.py not present (create it per agent-contract-enforcement.mdc)"
fi

# ---- 检查 5: User Story ↔ Test 漂移 ----（对应 test-philosophy.mdc）
section "user story / test alignment"
if [ -f .testing/user-stories/verify_quality.py ]; then
    if "$PYTHON_BIN" .testing/user-stories/verify_quality.py > /tmp/preflight-stories.log 2>&1; then
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

# ---- 检查 7: docs/approved/ 不变量（R1-R4 任何分支 + R5 仅 main/master） ----
# R1 frontmatter exists / R2 status valid / R3 pending+commits smell /
# R4 shipped without commits — enforced by dev-rules/scripts/check_approved_docs.py
# (universal across all consumer projects).
# R5 approved_by: pending — branch-specific, kept inline because it only blocks
# on main/master (other branches may legitimately carry pending approvers).
section "approved-doc invariants (R1-R4 universal + R5 main/master only)"
if [ -d docs/approved ]; then
    if [ -f dev-rules/scripts/check_approved_docs.py ]; then
        if "$PYTHON_BIN" dev-rules/scripts/check_approved_docs.py 2> /tmp/preflight-approved.log; then
            ok "R1-R4: all approved-doc frontmatter invariants hold"
        else
            cat /tmp/preflight-approved.log | sed 's/^/    /'
            fail "R1-R4: approved-doc invariants violated (see above)"
        fi
    else
        skip "dev-rules/scripts/check_approved_docs.py not present"
    fi

    if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
        pending=$(grep -lE '^approved_by:[[:space:]]*pending[[:space:]]*$' docs/approved/*.md 2>/dev/null || true)
        if [ -n "$pending" ]; then
            echo "$pending" | sed 's/^/    - /'
            fail "R5: files with approved_by: pending must not land on $branch"
        else
            ok "R5: all approved/* files on $branch have a real approver"
        fi
    else
        skip "R5 (approved_by: pending) only enforced on main/master, current=$branch"
    fi
else
    skip "docs/approved/ directory not present"
fi

# ---- 检查 8: 散文档中的 stat 块与 live 计算值一致 ----（治"变更必伴漂移"）
section "doc stats vs live values (sync-stats.sh --check)"
if [ -x dev-rules/sync-stats.sh ]; then
    if [ "$FIX_MODE" -eq 1 ]; then
        dev-rules/sync-stats.sh --update | sed 's/^/    /'
        ok "stat blocks updated to live values"
    else
        if dev-rules/sync-stats.sh --check > /tmp/preflight-stats.log 2>&1; then
            ok "all stat blocks match live values"
        else
            cat /tmp/preflight-stats.log | sed 's/^/    /'
            fail "doc stats have drifted (re-run with --fix or 'dev-rules/sync-stats.sh --update')"
        fi
    fi
else
    skip "dev-rules/sync-stats.sh not available"
fi

echo ""
if [ $errors -eq 0 ]; then
    echo "=== preflight: PASS ==="
    exit 0
else
    echo "=== preflight: FAIL ($errors check(s) failed) ==="
    exit 1
fi
