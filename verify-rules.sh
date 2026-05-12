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
PERSONAS_DIR="$SCRIPT_DIR/personas"
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

# ── twin persona files present ─────────────────────────────────────────
# personas/ is the single source of truth for supervisor + worker persona.
# Missing files break the twin harness silently, so assert them here.
section "twin persona files present"
for required in personas/supervisor-persona.md personas/worker-persona.md; do
    if [ -f "$SCRIPT_DIR/$required" ]; then
        ok "$required"
    else
        fail "$required missing"
    fi
done

section "twin workspace does not own persona"
if grep -R "persona snapshot\|copy the persona\|workspace / \"worker-persona.md\"\|workspace / \"supervisor-persona.md\"\|read_text_file" \
        "$SCRIPT_DIR/scripts/twin" "$SCRIPT_DIR/docs" "$SCRIPT_DIR/commands" "$SCRIPT_DIR/schemas" \
        > /dev/null 2>&1; then
    fail "twin must use DEV_RULES/personas persona files directly, not workspace persona snapshots"
else
    ok "twin runtime/docs do not require workspace persona snapshots"
fi

section "twin persona source path"
old_persona_path_found=0
persona_scan_files=("$SCRIPT_DIR/sync.sh" "$SCRIPT_DIR/.gitignore")
while IFS= read -r file; do
    persona_scan_files+=("$file")
done < <(find "$SCRIPT_DIR/scripts/twin" "$SCRIPT_DIR/docs" "$SCRIPT_DIR/commands" -type f ! -name '*.pyc' ! -path '*/__pycache__/*')
for forbidden in "~/.xuejiao-twin" ".xuejiao-twin" "secure-twin-persona" "TWIN_HOME"; do
    if grep -F "$forbidden" "${persona_scan_files[@]}" > /dev/null 2>&1; then
        old_persona_path_found=1
    fi
done
if [ "$old_persona_path_found" -eq 1 ]; then
    fail "twin must not use ~/.xuejiao-twin or secure persona snapshots; use DEV_RULES/personas directly"
else
    ok "twin uses DEV_RULES/personas directly"
fi

section "twin persona source is read-only"
if grep -q 'disallowed_tools=worker_disallowed_tools()' "$SCRIPT_DIR/scripts/twin/worker.py" && \
   grep -q 'Self-verification before ACCEPTED_DONE' "$PERSONAS_DIR/supervisor-persona.md"; then
    ok "worker denies persona source writes and supervisor self-verifies before ACCEPTED_DONE"
else
    fail "twin worker must deny persona writes and supervisor persona must include Self-verification section"
fi

# ── rule carrier partition anchor ─────────────────────────────────────
# Prevent the system-level simplification rule from drifting into another
# prose-only promise. The detailed partition belongs in dev-rules-convention;
# README/global/commands may only point at it.
section "rule carrier partition anchor"
if grep -q '<!-- rule-carrier-partition-anchor -->' "$RULES_DIR/dev-rules-convention.mdc" && \
   grep -q '<!-- rule-carrier-partition-pointer -->' "$README"; then
    ok "rule carrier partition has one documented anchor and README pointer"
else
    fail "rule carrier partition marker missing from dev-rules-convention.mdc or README"
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
