#!/usr/bin/env bash
# dev-rules 源仓库自己的提交门禁；消费项目使用 templates/preflight.sh。

set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

errors=0
section() { echo ""; echo "=== $* ==="; }
fail()    { echo "  FAIL: $*"; errors=$((errors + 1)); }
ok()      { echo "  ok: $*"; }

section "verify-rules.sh (dev-rules repo integrity)"
if [ -x ./verify-rules.sh ]; then
    if ./verify-rules.sh --quiet; then
        ok "all repo-integrity checks pass"
    else
        fail "verify-rules.sh found violations (re-run without --quiet for detail)"
    fi
else
    fail "./verify-rules.sh missing or not executable"
fi

section "sync-stats.sh --check (doc stats vs live values)"
if [ -x ./sync-stats.sh ]; then
    if ./sync-stats.sh --check > /tmp/dev-rules-stats.log 2>&1; then
        ok "all stat blocks match live values"
    else
        cat /tmp/dev-rules-stats.log | sed 's/^/    /'
        fail "stat drift (run: ./sync-stats.sh --update)"
    fi
else
    fail "./sync-stats.sh missing or not executable"
fi

section "xuejiao_twin fixtures (schema, privacy, dry-run)"
# Scrub GIT_* env so the fixture's nested `git init/add/commit` inside a
# TemporaryDirectory does not leak into the parent repo when this script
# runs from a git pre-commit hook (which sets GIT_DIR / GIT_INDEX_FILE).
if env -u GIT_DIR -u GIT_INDEX_FILE -u GIT_WORK_TREE -u GIT_AUTHOR_DATE -u GIT_COMMITTER_DATE -u GIT_EDITOR -u GIT_PREFIX -u GIT_INTERNAL_GETTEXT_SH_SCHEME python3 -m scripts.xuejiao_twin validate --fixtures > /tmp/dev-rules-xuejiao-twin.log 2>&1; then
    ok "xuejiao_twin fixture validation passes"
else
    cat /tmp/dev-rules-xuejiao-twin.log | sed 's/^/    /'
    fail "xuejiao_twin fixture validation failed"
fi

echo ""
if [ "$errors" -eq 0 ]; then
    echo "=== preflight: PASS ==="
    exit 0
else
    echo "=== preflight: FAIL ($errors check(s) failed) ==="
    exit 1
fi
