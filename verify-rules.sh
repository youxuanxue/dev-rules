#!/usr/bin/env bash
#
# dev-rules/verify-rules.sh — verify the rules repo is internally consistent.
#
# Section names (below) document scope. Do not enumerate count anywhere —
# the script is the source of truth; descriptive numbers in prose drift.
#
# Usage:
#   ./verify-rules.sh           # full output, non-zero exit on any failure
#   ./verify-rules.sh --quiet   # only emit on failure

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RULES_DIR="$SCRIPT_DIR/rules"
COMMANDS_DIR="$SCRIPT_DIR/commands"
GLOBAL_DIR="$SCRIPT_DIR/global"
README="$SCRIPT_DIR/README.md"

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

errors=0
log()     { [ "$QUIET" -eq 0 ] && echo "$@"; }
section() { log ""; log "── $* ──"; }
fail()    { echo "  FAIL: $*"; errors=$((errors + 1)); }
ok()      { [ "$QUIET" -eq 0 ] && echo "  ok: $*"; }

log "=== verify-rules: dev-rules repo integrity ==="

# ── frontmatter on every rule ──────────────────────────────────────────
# Cursor refuses to load .mdc files without the YAML envelope.
section "frontmatter on every .mdc"
checked=0
for rule in "$RULES_DIR"/*.mdc; do
    base="$(basename "$rule")"
    checked=$((checked + 1))
    head -n 1 "$rule" | grep -q '^---$' || { fail "$base: missing leading ---"; continue; }
    grep -q '^description:' "$rule" || fail "$base: missing description field"
    grep -q '^alwaysApply:' "$rule" || fail "$base: missing alwaysApply field"
    [ "$(grep -c '^---$' "$rule")" -ge 2 ] || fail "$base: missing closing ---"
done
ok "$checked rule(s) validated"

# ── every documented path resolves to a real file ──────────────────────
# Catches "wrote a path in some doc but the file moved/was deleted".
# Two patterns:
#   - rules/commands/templates/schemas/global only live inside dev-rules,
#     so bare and prefixed forms both resolve under SCRIPT_DIR.
#   - scripts/ exists BOTH at project root and at dev-rules/scripts/, so
#     only the explicit `dev-rules/scripts/...` form is dev-rules-rooted.
#     Bare `scripts/...` is intentionally per-project; do not validate.
section "every documented path resolves"
while IFS= read -r raw; do
    [ -z "$raw" ] && continue
    clean="${raw#\`}"; clean="${clean%\`}"
    rel="${clean#dev-rules/}"
    target="$SCRIPT_DIR/$rel"
    if [ -e "$target" ]; then
        ok "$clean"
    else
        fail "$clean referenced but missing at $target"
    fi
done < <({
    grep -rhoE '`(dev-rules/)?(rules|commands|templates|schemas|global)/[A-Za-z0-9_./-]+`' \
         "$RULES_DIR" "$COMMANDS_DIR" "$GLOBAL_DIR" "$README" 2>/dev/null
    grep -rhoE '`dev-rules/scripts/[A-Za-z0-9_./-]+`' \
         "$RULES_DIR" "$COMMANDS_DIR" "$GLOBAL_DIR" "$README" 2>/dev/null
} | sort -u)
sample=$(grep -rhoE '`(dev-rules/)?rules/[A-Za-z0-9_./-]+`' \
              "$RULES_DIR" "$COMMANDS_DIR" "$GLOBAL_DIR" "$README" 2>/dev/null | head -1)
[ -n "$sample" ] || fail "regex matched zero paths — likely broken"

# ── every rule/command is documented in README ─────────────────────────
# Forward direction of the path-resolves check above. Each rule's own
# `description:` frontmatter is its philosophical justification — a
# separate "philosophy mapping table" in README would just re-state that
# in marketing language and drift; deleted in favor of the description
# field, which the next check enforces is non-empty.
section "every rule/command appears in README"
if [ ! -f "$README" ]; then
    fail "README.md not found at $README"
else
    for rule in "$RULES_DIR"/*.mdc; do
        base="$(basename "$rule")"
        if grep -qE "(rules/$base|\`$base\`)" "$README"; then
            ok "$base"
        else
            fail "$base exists but not mentioned in README"
        fi
    done
    for cmd in "$COMMANDS_DIR"/*.md; do
        base="$(basename "$cmd")"
        name="${base%.md}"
        if grep -qE "(commands/$base|\`$base\`|/user:$name|\`$name\`)" "$README"; then
            ok "$base"
        else
            fail "$base exists but not referenced in README"
        fi
    done
fi

# ── LaunchAgent reality matches doc promise (macOS dev only) ───────────
# §三 anti-drift: a doc claim ("agent runs every 30 min") has to be
# observable in launchctl, otherwise the cross-machine sync is fiction.
section "cross-machine sync agent installed (macOS dev only)"
HOME_CANONICAL="${DEV_RULES_HOME:-$HOME/Codes/dev-rules}"
LAUNCH_LABEL="local.dev-rules.sync"
LAUNCH_PLIST="$HOME/Library/LaunchAgents/${LAUNCH_LABEL}.plist"

if [ -n "${CI:-}" ]; then
    ok "skipped (CI environment)"
elif [ "$(uname)" != "Darwin" ]; then
    ok "skipped (non-macOS: $(uname))"
elif [ ! -d "$HOME_CANONICAL" ]; then
    ok "skipped ($HOME_CANONICAL not present — pure consumer machine)"
elif [ ! -f "$LAUNCH_PLIST" ]; then
    fail "LaunchAgent plist missing at $LAUNCH_PLIST"
    echo "    fix: bash $SCRIPT_DIR/templates/install-launchagent.sh"
elif ! command -v launchctl > /dev/null 2>&1; then
    ok "skipped (launchctl not available)"
else
    # Materialize listing before piping; `launchctl list | grep -q` gets
    # SIGPIPE'd (141) and would be misreported as "not loaded".
    listing="$(launchctl list 2>/dev/null || true)"
    if printf '%s\n' "$listing" | grep -qF "$LAUNCH_LABEL"; then
        ok "$LAUNCH_LABEL installed and loaded (--pull every 30 min)"
    else
        fail "LaunchAgent plist exists but not loaded into launchctl"
        echo "    fix: launchctl load $LAUNCH_PLIST"
    fi
fi

log ""
if [ "$errors" -eq 0 ]; then
    log "=== PASS ==="
    exit 0
else
    echo "=== FAIL: $errors error(s) ==="
    exit 1
fi
