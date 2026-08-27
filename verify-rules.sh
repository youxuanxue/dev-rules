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
SKILLS_LINK="$SCRIPT_DIR/.cursor/skills"

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
        [ -f "$cmd" ] || continue
        base="$(basename "$cmd")"
        name="${base%.md}"
        if grep -qE "(commands/$base|\`$base\`|/user:$name|\`$name\`)" "$README"; then
            ok "$base"
        else
            fail "$base exists but not referenced in README"
        fi
    done
fi

# ── Agent Skills source link ───────────────────────────────────────────
# Skills live in the shared agent-skills repo; dev-rules only carries a
# symlink so command/rule changes and skill changes do not fork sources.
section "agent skills source link"
if [ ! -L "$SKILLS_LINK" ]; then
    fail ".cursor/skills must be a symlink to ../../agent-skills"
elif [ "$(readlink "$SKILLS_LINK")" != "../../agent-skills" ]; then
    fail ".cursor/skills points to $(readlink "$SKILLS_LINK"), expected ../../agent-skills"
else
    ok ".cursor/skills -> ../../agent-skills"
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
        "$SCRIPT_DIR/scripts/twin" "$SCRIPT_DIR/docs" "$SCRIPT_DIR/schemas" \
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
done < <(find "$SCRIPT_DIR/scripts/twin" "$SCRIPT_DIR/docs" -type f ! -name '*.pyc' ! -path '*/__pycache__/*')
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

section "twin host workflow has one skill owner"
if [ ! -e "$COMMANDS_DIR/twin.md" ] && \
   grep -Fq 'agent-skills/twin/SKILL.md' "$GLOBAL_DIR/CLAUDE.md" && \
   ! grep -Fq 'Claude Command Surface' "$SCRIPT_DIR/docs/agent_integration.md" && \
   ! grep -Fq 'commands/twin.md' "$SCRIPT_DIR/scripts/export_agent_contract.py"; then
    ok "twin host workflow is owned only by the shared skill"
else
    fail "twin host workflow must not be duplicated in commands/twin.md or generated CLI docs"
fi

section "stale Claude command cleanup preserves user commands"
if (
    set -eu
    test_home="$(mktemp -d)"
    test_canonical="$test_home/Codes/dev-rules"
    cleanup() {
        rm -rf "$test_home"
    }
    trap cleanup EXIT

    mkdir -p "$test_canonical/commands" "$test_home/.claude/commands"
    touch "$test_canonical/commands/keep.md" "$test_home/user-command.md"
    ln -s "$test_canonical/commands/keep.md" "$test_home/.claude/commands/keep.md"
    ln -s "$test_canonical/commands/twin.md" "$test_home/.claude/commands/twin.md"
    ln -s "$test_home/user-command.md" "$test_home/.claude/commands/user.md"
    ln -s "$test_home/missing-command.md" "$test_home/.claude/commands/user-dangling.md"

    env -u CODEX_HOME -u ANTIGRAVITY_HOME \
        HOME="$test_home" DEV_RULES_HOME="$test_canonical" \
        bash "$SCRIPT_DIR/sync.sh" > "$test_home/sync.log"

    test ! -L "$test_home/.claude/commands/twin.md"
    test "$(readlink "$test_home/.claude/commands/keep.md")" = "$test_canonical/commands/keep.md"
    test "$(readlink "$test_home/.claude/commands/user.md")" = "$test_home/user-command.md"
    test "$(readlink "$test_home/.claude/commands/user-dangling.md")" = "$test_home/missing-command.md"
) > /tmp/dev-rules-command-cleanup.log 2>&1; then
    ok "sync removes stale managed commands without touching user-owned links"
else
    cat /tmp/dev-rules-command-cleanup.log | sed 's/^/    /'
    fail "sync must remove only stale dev-rules-managed command symlinks"
fi

section "home skill registry is additive and owner-safe"
if (
    set -eu
    test_home="$(mktemp -d)"
    test_canonical="$test_home/Codes/dev-rules"
    test_agent_skills="$test_home/Codes/agent-skills"
    assert() {
        "$@" || exit 1
    }
    cleanup() {
        rm -rf "$test_home"
    }
    trap cleanup EXIT

    mkdir -p "$test_agent_skills/demo" "$test_canonical/.cursor" \
        "$test_home/.cursor" "$test_home/.claude"
    touch "$test_agent_skills/demo/SKILL.md"

    # Phase A: migrate the pure legacy chain to an additive registry.
    ln -s "$test_agent_skills" "$test_canonical/.cursor/skills"
    ln -s "$test_canonical/.cursor/skills" "$test_home/.cursor/skills"
    ln -s "$test_home/.cursor/skills" "$test_home/.claude/skills"

    env -u CODEX_HOME -u ANTIGRAVITY_HOME \
        HOME="$test_home" DEV_RULES_HOME="$test_canonical" \
        bash "$SCRIPT_DIR/sync.sh" > "$test_home/phase-a.log"

    assert test -d "$test_home/.cursor/skills"
    assert test ! -L "$test_home/.cursor/skills"
    assert test "$(readlink "$test_home/.cursor/skills/demo")" = "$test_agent_skills/demo"
    assert test "$(readlink "$test_home/.claude/skills")" = "$test_home/.cursor/skills"

    # Phase B: preserve foreign and local registry entries on later reconciles.
    mkdir -p "$test_home/.twin/skills/twin" "$test_home/.cursor/skills/local-user"
    touch "$test_home/.twin/skills/twin/SKILL.md"
    ln -s "$test_home/.twin/skills/twin" "$test_home/.cursor/skills/twin"
    foreign_twin_target="$(readlink "$test_home/.cursor/skills/twin")"

    env -u CODEX_HOME -u ANTIGRAVITY_HOME \
        HOME="$test_home" DEV_RULES_HOME="$test_canonical" \
        bash "$SCRIPT_DIR/sync.sh" > "$test_home/phase-b.log"

    assert test -d "$test_home/.cursor/skills"
    assert test ! -L "$test_home/.cursor/skills"
    assert test "$(readlink "$test_home/.cursor/skills/demo")" = "$test_agent_skills/demo"
    assert test "$(readlink "$test_home/.cursor/skills/twin")" = "$foreign_twin_target"
    assert test -d "$test_home/.cursor/skills/local-user"
    assert test ! -L "$test_home/.cursor/skills/local-user"
    assert test "$(readlink "$test_home/.claude/skills")" = "$test_home/.cursor/skills"
) > /tmp/dev-rules-additive-skill-registry.log 2>&1; then
    ok "sync materializes the additive home skill registry and preserves foreign entries"
else
    cat /tmp/dev-rules-additive-skill-registry.log | sed 's/^/    /'
    fail "sync must materialize an additive home skill registry without replacing foreign entries"
fi

section "home skill registry fails closed on foreign ownership collisions"
if (
    set -eu
    test_home="$(mktemp -d)"
    test_canonical="$test_home/Codes/dev-rules"
    test_agent_skills="$test_home/Codes/agent-skills"
    assert() {
        "$@" || exit 1
    }
    cleanup() {
        rm -rf "$test_home"
    }
    trap cleanup EXIT

    mkdir -p "$test_agent_skills/collision" "$test_canonical/.cursor" \
        "$test_home/.cursor/skills" "$test_home/.foreign/collision"
    printf 'dev-rules skill\n' > "$test_agent_skills/collision/SKILL.md"
    printf 'foreign skill\n' > "$test_home/.foreign/collision/SKILL.md"
    ln -s "$test_agent_skills" "$test_canonical/.cursor/skills"
    ln -s "$test_home/.foreign/collision" "$test_home/.cursor/skills/collision"
    foreign_target="$(readlink "$test_home/.cursor/skills/collision")"
    cp "$test_home/.foreign/collision/SKILL.md" "$test_home/foreign-before"

    if env -u CODEX_HOME -u ANTIGRAVITY_HOME \
        HOME="$test_home" DEV_RULES_HOME="$test_canonical" \
        bash "$SCRIPT_DIR/sync.sh" > "$test_home/collision.log" 2>&1; then
        echo "expected sync collision to fail" >&2
        exit 1
    fi

    assert grep -Fq "ownership conflict" "$test_home/collision.log"
    assert test "$(readlink "$test_home/.cursor/skills/collision")" = "$foreign_target"
    assert cmp -s "$test_home/foreign-before" "$test_home/.foreign/collision/SKILL.md"
) > /tmp/dev-rules-additive-skill-collision.log 2>&1; then
    ok "sync fails closed and preserves foreign same-name skill links"
else
    cat /tmp/dev-rules-additive-skill-collision.log | sed 's/^/    /'
    fail "sync must fail closed on foreign same-name skill links"
fi

section "home skill registry preserves foreign dot-dot escape links"
if (
    set -eu
    test_home="$(mktemp -d)"
    test_canonical="$test_home/Codes/dev-rules"
    test_agent_skills="$test_home/Codes/agent-skills"
    assert() {
        "$@" || exit 1
    }
    cleanup() {
        rm -rf "$test_home"
    }
    trap cleanup EXIT

    mkdir -p "$test_agent_skills/demo" "$test_canonical/.cursor" \
        "$test_home/.cursor/skills" "$test_home/Codes/foreign/old"
    touch "$test_agent_skills/demo/SKILL.md"
    printf 'foreign stale skill\n' > "$test_home/Codes/foreign/old/SKILL.md"
    ln -s "$test_agent_skills" "$test_canonical/.cursor/skills"
    ln -s "$test_agent_skills/../foreign/old" "$test_home/.cursor/skills/old"
    escaped_target="$(readlink "$test_home/.cursor/skills/old")"
    cp "$test_home/Codes/foreign/old/SKILL.md" "$test_home/foreign-before"

    env -u CODEX_HOME -u ANTIGRAVITY_HOME \
        HOME="$test_home" DEV_RULES_HOME="$test_canonical" \
        bash "$SCRIPT_DIR/sync.sh" > "$test_home/escape.log"

    assert test -L "$test_home/.cursor/skills/old"
    assert test "$(readlink "$test_home/.cursor/skills/old")" = "$escaped_target"
    assert cmp -s "$test_home/foreign-before" "$test_home/Codes/foreign/old/SKILL.md"
) > /tmp/dev-rules-additive-skill-dot-dot-escape.log 2>&1; then
    ok "sync preserves foreign stale links that escape the configured source with .."
else
    cat /tmp/dev-rules-additive-skill-dot-dot-escape.log | sed 's/^/    /'
    fail "sync must preserve foreign stale links that escape the configured source with .."
fi

section "home skill registry rejects unsafe consumer destination roots"
if (
    set -eu
    test_root="$(mktemp -d)"
    assert() {
        "$@" || exit 1
    }
    cleanup() {
        rm -rf "$test_root"
    }
    trap cleanup EXIT

    setup_fixture() {
        local fixture_home="$1"
        local fixture_canonical="$fixture_home/Codes/dev-rules"
        local fixture_agent_skills="$fixture_home/Codes/agent-skills"
        mkdir -p "$fixture_agent_skills/demo" "$fixture_canonical/.cursor" "$fixture_home/.cursor"
        touch "$fixture_agent_skills/demo/SKILL.md"
        ln -s "$fixture_agent_skills" "$fixture_canonical/.cursor/skills"
    }

    cursor_home="$test_root/cursor"
    setup_fixture "$cursor_home"
    printf 'cursor root\n' > "$cursor_home/.cursor/skills"
    cp "$cursor_home/.cursor/skills" "$cursor_home/cursor-before"
    if env -u CODEX_HOME -u ANTIGRAVITY_HOME \
        HOME="$cursor_home" DEV_RULES_HOME="$cursor_home/Codes/dev-rules" \
        bash "$SCRIPT_DIR/sync.sh" > "$cursor_home/cursor.log" 2>&1; then
        echo "expected Cursor skills-root collision to fail" >&2
        exit 1
    fi
    assert grep -Fq "ownership conflict" "$cursor_home/cursor.log"
    assert cmp -s "$cursor_home/cursor-before" "$cursor_home/.cursor/skills"

    codex_home="$test_root/codex"
    setup_fixture "$codex_home"
    mkdir -p "$codex_home/.codex"
    printf 'codex root\n' > "$codex_home/.codex/skills"
    cp "$codex_home/.codex/skills" "$codex_home/codex-before"
    if env -u ANTIGRAVITY_HOME \
        HOME="$codex_home" DEV_RULES_HOME="$codex_home/Codes/dev-rules" \
        CODEX_HOME="$codex_home/.codex" \
        bash "$SCRIPT_DIR/sync.sh" > "$codex_home/codex.log" 2>&1; then
        echo "expected Codex skills-root collision to fail" >&2
        exit 1
    fi
    assert grep -Fq "ownership conflict" "$codex_home/codex.log"
    assert cmp -s "$codex_home/codex-before" "$codex_home/.codex/skills"

    antigravity_home="$test_root/antigravity"
    setup_fixture "$antigravity_home"
    mkdir -p "$antigravity_home/.gemini/antigravity-cli" "$antigravity_home/.foreign/skills"
    ln -s "$antigravity_home/.foreign/skills" "$antigravity_home/.gemini/antigravity-cli/skills"
    if env -u CODEX_HOME \
        HOME="$antigravity_home" DEV_RULES_HOME="$antigravity_home/Codes/dev-rules" \
        ANTIGRAVITY_HOME="$antigravity_home/.gemini/antigravity-cli" \
        bash "$SCRIPT_DIR/sync.sh" > "$antigravity_home/antigravity.log" 2>&1; then
        echo "expected Antigravity skills-root collision to fail" >&2
        exit 1
    fi
    assert grep -Fq "ownership conflict" "$antigravity_home/antigravity.log"
    assert test ! -e "$antigravity_home/.foreign/skills/demo"
) > /tmp/dev-rules-additive-skill-destination-roots.log 2>&1; then
    ok "sync fails closed for unsafe Cursor, Codex, and Antigravity skill roots"
else
    cat /tmp/dev-rules-additive-skill-destination-roots.log | sed 's/^/    /'
    fail "sync must fail closed for unsafe Cursor, Codex, and Antigravity skill roots"
fi

section "twin persona source is read-only"
if grep -q 'disallowed_tools=worker_disallowed_tools()' "$SCRIPT_DIR/scripts/twin/worker.py" && \
   grep -q 'Self-verification before accepted_done' "$PERSONAS_DIR/supervisor-persona.md"; then
    ok "worker denies persona source writes and supervisor self-verifies before accepted_done"
else
    fail "twin worker must deny persona writes and supervisor persona must include Self-verification section"
fi

section "global hook self-tests"
if python3 "$GLOBAL_DIR/hooks/gh-pr-guard.py" --self-test > /tmp/dev-rules-gh-pr-guard.log 2>&1; then
    ok "gh-pr-guard.py self-test"
else
    cat /tmp/dev-rules-gh-pr-guard.log | sed 's/^/    /'
    fail "gh-pr-guard.py self-test failed"
fi

section "install-hooks linked worktree self-test"
if (
    set -eu
    tmp="$(mktemp -d)"
    wt="${tmp}-wt"
    unset_git_env() {
        unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_NAMESPACE \
              GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES \
              GIT_COMMON_DIR GIT_PREFIX
    }
    git_clean() (
        unset_git_env
        git "$@"
    )
    cleanup() {
        git_clean -C "$tmp" worktree remove -f "$wt" >/dev/null 2>&1 || true
        rm -rf "$tmp" "$wt"
    }
    trap cleanup EXIT

    git_clean -C "$tmp" init -q
    git_clean -C "$tmp" config user.email t@t
    git_clean -C "$tmp" config user.name t
    git_clean -C "$tmp" commit --allow-empty -m base >/dev/null
    git_clean -C "$tmp" worktree add -q --detach "$wt" HEAD
    ln -s "$SCRIPT_DIR" "$wt/dev-rules"

    cd "$wt"
    unset_git_env
    bash dev-rules/templates/install-hooks.sh
    pre_commit="$(git rev-parse --git-path hooks/pre-commit)"
    commit_msg="$(git rev-parse --git-path hooks/commit-msg)"
    test -x "$pre_commit"
    test -x "$commit_msg"
    bash -n "$pre_commit" "$commit_msg"
) > /tmp/dev-rules-install-hooks-worktree.log 2>&1; then
    ok "install-hooks.sh works from linked worktree"
else
    cat /tmp/dev-rules-install-hooks-worktree.log | sed 's/^/    /'
    fail "install-hooks.sh must install hooks from linked worktrees"
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

# ── check_*.py / gen_*.py self-tests ──────────────────────────────────
# Mechanical assurance: check/gen scripts that ship a --self-test mode must
# pass their own assertions. Prevents the "check 自己没被检查" anti-pattern.
section "check_*.py / gen_*.py self-tests"
SECTION_TESTED=0
for script in "$SCRIPT_DIR"/scripts/check_*.py "$SCRIPT_DIR"/scripts/gen_*.py; do
    [ -f "$script" ] || continue
    if "$script" --help 2>/dev/null | grep -q -- "--self-test"; then
        SECTION_TESTED=1
        if "$script" --self-test > /tmp/dev-rules-self-test.log 2>&1; then
            ok "$(basename "$script") --self-test"
        else
            cat /tmp/dev-rules-self-test.log | sed 's/^/    /'
            fail "$(basename "$script") --self-test failed"
        fi
    fi
done
if [ "$SECTION_TESTED" = "0" ]; then
    ok "no check_*.py exposes --self-test mode"
fi

# ── global/bin launcher self-tests ────────────────────────────────────
# Same "check 自己没被检查" guard as above, extended to CLI launchers. A
# launcher that ships --self-test must pass it here, otherwise the self-test
# only runs when someone remembers to — exactly the soft constraint §5 says
# to harden.
#
# Discovery is static (grep the source for a --self-test case branch), not
# `$launcher --help`: probing help would execute the launcher, and some of
# these exec real backends (claude-with-token exec's claude with a token).
# Symlinks are skipped so profile aliases don't re-run the same script.
section "global/bin launcher self-tests"
LAUNCHER_TESTED=0
if [ -d "$GLOBAL_DIR/bin" ]; then
    for launcher in "$GLOBAL_DIR"/bin/*; do
        [ -f "$launcher" ] || continue
        [ -L "$launcher" ] && continue
        grep -q -- '--self-test)' "$launcher" || continue
        LAUNCHER_TESTED=1
        if "$launcher" --self-test > /tmp/dev-rules-launcher-self-test.log 2>&1; then
            ok "$(basename "$launcher") --self-test"
        else
            sed 's/^/    /' /tmp/dev-rules-launcher-self-test.log
            fail "$(basename "$launcher") --self-test failed"
        fi
    done
fi
if [ "$LAUNCHER_TESTED" = "0" ]; then
    ok "no global/bin launcher exposes --self-test mode"
fi

# ── twin worktree isolation self-test ─────────────────────────────────
# worktree.py runs its assertions when invoked directly (no --self-test
# flag), so the generic check_*/gen_* loop above does not reach it. Run it
# explicitly: it exercises deterministic path/branch derivation, the env gate,
# shared wtree.py create/reuse, fail-closed resolution, and safe cleanup.
section "twin worktree isolation self-test"
if python3 "$SCRIPT_DIR/scripts/twin/worktree.py" > /tmp/dev-rules-twin-worktree.log 2>&1; then
    ok "scripts/twin/worktree.py selftest"
else
    sed 's/^/    /' /tmp/dev-rules-twin-worktree.log
    fail "scripts/twin/worktree.py selftest failed"
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
