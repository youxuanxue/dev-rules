#!/usr/bin/env bash
#
# scripts/preflight.sh — dev-rules SOURCE repo's own commit gate.
#
# Why this exists:
#   templates/preflight.sh is generic and assumes a consumer-project layout
#   (`dev-rules/sync.sh`, `dev-rules/sync-stats.sh`, …). Inside the source
#   repo those paths collapse to `./sync.sh` etc., so the template skips the
#   most important checks and the dev-rules repo would silently bypass its
#   own discipline. install-hooks.sh's fallback chain (project wrapper →
#   template) lets us plug a source-repo-aware wrapper in here.
#
# Checks (mechanical — no "remember to run X"):
#   1. verify-rules.sh                — repo integrity (frontmatter, README
#                                       coverage, ghost paths, LaunchAgent)
#   2. sync-stats.sh --check          — every <!-- stat:NAME --> block in
#                                       prose matches the live computed value
#
# Both gates are also wired so consumer projects' preflight § 3 + § 8 catch
# them, but inside the source repo we run them directly because the
# preflight template can't reach `./verify-rules.sh` (it looks for the
# submodule path).
#
# Bypass with --no-verify is reserved for emergency reverts only.

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

echo ""
if [ "$errors" -eq 0 ]; then
    echo "=== preflight: PASS ==="
    exit 0
else
    echo "=== preflight: FAIL ($errors check(s) failed) ==="
    exit 1
fi
