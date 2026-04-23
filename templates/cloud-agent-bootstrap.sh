#!/usr/bin/env bash
#
# dev-rules/templates/cloud-agent-bootstrap.sh — generic runtime-env
# bootstrapper for both Cursor Cloud Agents and local Cursor sessions.
#
# Why this lives in dev-rules (not per-project):
#   Every project that wants its agent to drive `claude`, `gh`, `jq`, …
#   used to copy a near-identical install script (see the original
#   tk/sub2api/.cursor/cloud-agent-install.sh + scripts/setup-claude-code.sh).
#   That copy-paste is exactly the failure mode dev-rules exists to kill:
#   one fix in the recipe → N stale forks. This script is the single
#   source of truth; projects only declare WHAT they need (tools +
#   secrets) in `.cursor/cloud-agent.env`, never HOW it's installed.
#
# Modes:
#   bash dev-rules/templates/cloud-agent-bootstrap.sh
#                              install missing tools + write claude settings + check
#   bash dev-rules/templates/cloud-agent-bootstrap.sh --check
#                              read-only: report missing tools/secrets/settings,
#                              non-zero exit if any REQUIRED item is missing.
#                              Used by preflight § 9 and by `.cursor/environment.json`
#                              for fast self-test.
#   bash dev-rules/templates/cloud-agent-bootstrap.sh --print-config
#                              dump the resolved config (debugging).
#
# Project config: $REPO_ROOT/.cursor/cloud-agent.env
#   Sourced as bash. Declares the agent's runtime contract.
#   See `dev-rules/templates/cloud-agent.env.example` for the full shape.
#   When the file is absent, the script falls back to "claude only"
#   defaults so a brand-new project at least gets the Claude Code CLI path.
#
# Exit codes:
#   0  install/check passed (warnings are non-fatal)
#   1  at least one REQUIRED tool or secret is missing
#   2  bad CLI args
set -u

MODE="install"
case "${1:-}" in
    "")             MODE="install" ;;
    --check)        MODE="check" ;;
    --print-config) MODE="print" ;;
    -h|--help)
        sed -nE '/^# Modes:/,/^# Exit codes:/p' "$0" | sed -E 's/^# ?//'
        exit 0
        ;;
    *)
        echo "cloud-agent-bootstrap: unknown arg '$1' (try --help)" >&2
        exit 2
        ;;
esac

# Resolve project root (same strategy as preflight.sh)
if [ -n "${CLOUD_AGENT_REPO_ROOT:-}" ] && [ -d "$CLOUD_AGENT_REPO_ROOT" ]; then
    REPO_ROOT="$CLOUD_AGENT_REPO_ROOT"
elif git_top="$(git rev-parse --show-superproject-working-tree 2>/dev/null)" && [ -n "$git_top" ]; then
    REPO_ROOT="$git_top"
elif git_top="$(git rev-parse --show-toplevel 2>/dev/null)" && [ -n "$git_top" ]; then
    REPO_ROOT="$git_top"
else
    REPO_ROOT="$(pwd)"
fi

CONFIG_FILE="$REPO_ROOT/.cursor/cloud-agent.env"

# Defaults applied BEFORE sourcing so set -u doesn't trip on a partial config.
# A bare project (no .cursor/cloud-agent.env) gets the "claude-only" minimum:
# the Claude Code CLI on PATH and ANTHROPIC_API_KEY in the env. Anything more
# (gh, jq, gateway settings, project hooks) is opt-in per project.
CLOUD_AGENT_TOOLS="${CLOUD_AGENT_TOOLS:-claude}"
CLOUD_AGENT_REQUIRED_SECRETS="${CLOUD_AGENT_REQUIRED_SECRETS:-ANTHROPIC_API_KEY}"
CLOUD_AGENT_OPTIONAL_SECRETS="${CLOUD_AGENT_OPTIONAL_SECRETS:-}"
CLOUD_AGENT_CLAUDE_BASE_URL="${CLOUD_AGENT_CLAUDE_BASE_URL:-}"
CLOUD_AGENT_CLAUDE_TOKEN_VAR="${CLOUD_AGENT_CLAUDE_TOKEN_VAR:-ANTHROPIC_AUTH_TOKEN}"
CLOUD_AGENT_PROJECT_HOOK="${CLOUD_AGENT_PROJECT_HOOK:-}"

if [ -f "$CONFIG_FILE" ]; then
    # shellcheck disable=SC1090
    . "$CONFIG_FILE"
fi

errors=0
warnings=0
log()  { echo "[cloud-agent] $*"; }
warn() { echo "[cloud-agent][warn] $*" >&2; warnings=$((warnings + 1)); }
fail() { echo "[cloud-agent][FAIL] $*" >&2; errors=$((errors + 1)); }
ok()   { echo "[cloud-agent]   ok: $*"; }

if [ "$MODE" = "print" ]; then
    echo "REPO_ROOT=$REPO_ROOT"
    echo "CONFIG_FILE=$CONFIG_FILE $([ -f "$CONFIG_FILE" ] && echo '(found)' || echo '(missing, using defaults)')"
    echo "CLOUD_AGENT_TOOLS='$CLOUD_AGENT_TOOLS'"
    echo "CLOUD_AGENT_REQUIRED_SECRETS='$CLOUD_AGENT_REQUIRED_SECRETS'"
    echo "CLOUD_AGENT_OPTIONAL_SECRETS='$CLOUD_AGENT_OPTIONAL_SECRETS'"
    echo "CLOUD_AGENT_CLAUDE_BASE_URL='${CLOUD_AGENT_CLAUDE_BASE_URL:-(unset → Anthropic SaaS)}'"
    echo "CLOUD_AGENT_CLAUDE_TOKEN_VAR='$CLOUD_AGENT_CLAUDE_TOKEN_VAR'"
    echo "CLOUD_AGENT_PROJECT_HOOK='${CLOUD_AGENT_PROJECT_HOOK:-(none)}'"
    exit 0
fi

# ---------------------------------------------------------------------------
# Tool installers (install mode only)
# ---------------------------------------------------------------------------
# Each installer is no-op when the tool is already present (idempotent).
# On macOS local-dev machines tools are normally pre-installed → check phase
# is sufficient. On cloud agent VMs (fresh Linux) the install path runs.
# ---------------------------------------------------------------------------

install_claude() {
    command -v claude >/dev/null 2>&1 && return 0
    if ! command -v npm >/dev/null 2>&1; then
        warn "npm not found; cannot install claude (install Node.js+npm first or via project hook)"
        return 1
    fi
    log "installing @anthropic-ai/claude-code via npm"
    npm install -g @anthropic-ai/claude-code >/dev/null 2>&1 \
        || { warn "npm install -g @anthropic-ai/claude-code failed"; return 1; }
}

install_gh() {
    command -v gh >/dev/null 2>&1 && return 0
    log "installing gh (GitHub CLI)"
    if command -v apt-get >/dev/null 2>&1; then
        # Try the simple path first (works on Ubuntu 22.04+ where gh is in
        # universe). Fall back to the official GitHub CLI APT source (needed
        # on Ubuntu 20.04 / Debian images). Recipe lifted verbatim from
        # tk/sub2api/.cursor/cloud-agent-install.sh, which had it battle-
        # tested against Cursor's stock cloud-agent image.
        if sudo apt-get update -qq 2>/dev/null && sudo apt-get install -y -qq gh 2>/dev/null; then
            return 0
        fi
        log "  simple apt install failed; adding official GitHub CLI APT source"
        if command -v curl >/dev/null 2>&1 || sudo apt-get install -y -qq curl 2>/dev/null; then
            sudo mkdir -p -m 755 /etc/apt/keyrings
            curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
                | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null \
                && sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
                && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
                    | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null \
                && sudo apt-get update -qq \
                && sudo apt-get install -y -qq gh \
                && return 0
        fi
        warn "gh install via apt failed (install manually if needed)"
        return 1
    elif command -v brew >/dev/null 2>&1; then
        brew install gh >/dev/null 2>&1 || { warn "gh install via brew failed"; return 1; }
        return 0
    else
        warn "no apt-get/brew available; cannot install gh"
        return 1
    fi
}

install_jq() {
    command -v jq >/dev/null 2>&1 && return 0
    log "installing jq"
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -qq 2>/dev/null || true
        sudo apt-get install -y -qq jq 2>/dev/null && return 0
        warn "jq install via apt failed"
        return 1
    elif command -v brew >/dev/null 2>&1; then
        brew install jq >/dev/null 2>&1 && return 0
        warn "jq install via brew failed"
        return 1
    else
        warn "no apt-get/brew available; cannot install jq"
        return 1
    fi
}

install_tool() {
    local t="$1"
    case "$t" in
        claude)     install_claude ;;
        gh)         install_gh ;;
        jq)         install_jq ;;
        # awscli / aws / docker / pnpm / node etc. are intentionally NOT
        # auto-installed: they are heavy, distro-coupled, or contradict
        # OPC's "least credentials" stance (the long-lived AWS creds case).
        # Projects that genuinely need them install via CLOUD_AGENT_PROJECT_HOOK.
        *)
            log "  no auto-installer for '$t' — checking PATH only (declare in CLOUD_AGENT_PROJECT_HOOK if install needed)"
            return 0
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Claude settings.json writer
# ---------------------------------------------------------------------------
# Only writes when the project declares a self-hosted gateway URL. For
# Anthropic SaaS the CLI reads ANTHROPIC_API_KEY directly from env — no
# settings.json needed. This keeps the abstraction minimal and avoids
# baking opinionated knobs (effortLevel, MAX_THINKING_TOKENS, …) into
# dev-rules. Projects that want those knobs add them via the project hook.
# ---------------------------------------------------------------------------

write_claude_settings() {
    local base_url="$1" token_var="$2"
    local token="${!token_var:-}"
    if [ -z "$token" ]; then
        # check phase will report this as a missing required secret if the
        # project listed $token_var in CLOUD_AGENT_REQUIRED_SECRETS.
        return 0
    fi
    mkdir -p "$HOME/.claude"
    umask 077
    local settings_file="$HOME/.claude/settings.json"
    cat > "$settings_file" <<EOF
{
  "env": {
    "ANTHROPIC_BASE_URL": "$base_url",
    "ANTHROPIC_AUTH_TOKEN": "$token"
  }
}
EOF
    log "wrote $settings_file (gateway $base_url)"
}

# ---------------------------------------------------------------------------
# INSTALL phase
# ---------------------------------------------------------------------------
if [ "$MODE" = "install" ]; then
    log "config: $CONFIG_FILE $([ -f "$CONFIG_FILE" ] && echo '' || echo '(missing → defaults)')"
    log "tools to ensure: $CLOUD_AGENT_TOOLS"
    for t in $CLOUD_AGENT_TOOLS; do
        install_tool "$t" || true
    done
    if [ -n "$CLOUD_AGENT_CLAUDE_BASE_URL" ]; then
        write_claude_settings "$CLOUD_AGENT_CLAUDE_BASE_URL" "$CLOUD_AGENT_CLAUDE_TOKEN_VAR"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK phase (always runs — install includes a self-test at the end)
# ---------------------------------------------------------------------------
log ""
log "=== cloud-agent env check ==="

log "tools:"
for t in $CLOUD_AGENT_TOOLS; do
    if command -v "$t" >/dev/null 2>&1; then
        ok "$t ($(command -v "$t"))"
    else
        fail "$t not on PATH (install via cloud-agent-bootstrap.sh, or add to CLOUD_AGENT_PROJECT_HOOK)"
    fi
done

log "required secrets:"
if [ -z "$CLOUD_AGENT_REQUIRED_SECRETS" ]; then
    log "  (none declared)"
fi
for s in $CLOUD_AGENT_REQUIRED_SECRETS; do
    if [ -n "${!s:-}" ]; then
        # Show only a 4-char prefix for confirmation; never echo the secret.
        prefix="${!s:0:4}"
        ok "$s set (prefix='${prefix}…')"
    else
        fail "$s not set — cloud: Cursor Dashboard → Cloud Agents → Secrets; local: export in shell rc"
    fi
done

log "optional secrets:"
if [ -z "$CLOUD_AGENT_OPTIONAL_SECRETS" ]; then
    log "  (none declared)"
fi
for s in $CLOUD_AGENT_OPTIONAL_SECRETS; do
    if [ -n "${!s:-}" ]; then
        prefix="${!s:0:4}"
        ok "$s set (prefix='${prefix}…')"
    else
        warn "$s not set (optional capability disabled)"
    fi
done

if [ -n "$CLOUD_AGENT_CLAUDE_BASE_URL" ]; then
    log "claude gateway settings:"
    SETTINGS="$HOME/.claude/settings.json"
    if [ -s "$SETTINGS" ]; then
        if grep -q "$CLOUD_AGENT_CLAUDE_BASE_URL" "$SETTINGS" 2>/dev/null; then
            ok "$SETTINGS points at $CLOUD_AGENT_CLAUDE_BASE_URL"
        else
            warn "$SETTINGS exists but does not reference $CLOUD_AGENT_CLAUDE_BASE_URL"
        fi
    else
        if [ "$MODE" = "check" ]; then
            warn "$SETTINGS missing — run cloud-agent-bootstrap.sh (no --check) to create it"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Project hook (install only) — runs AFTER the env check so a project hook
# never papers over a broken env. If the env check failed and we're in install
# mode, still try the hook (some projects want progress on what they can),
# but the script exits non-zero overall.
# ---------------------------------------------------------------------------
if [ "$MODE" = "install" ] && [ -n "$CLOUD_AGENT_PROJECT_HOOK" ]; then
    log ""
    log "=== running project hook: $CLOUD_AGENT_PROJECT_HOOK ==="
    ( cd "$REPO_ROOT" && bash -c "$CLOUD_AGENT_PROJECT_HOOK" ) || warn "project hook exited non-zero"
fi

log ""
if [ "$errors" -eq 0 ]; then
    log "=== cloud-agent: PASS ($warnings warning(s)) ==="
    exit 0
else
    log "=== cloud-agent: FAIL ($errors error(s), $warnings warning(s)) ==="
    exit 1
fi
