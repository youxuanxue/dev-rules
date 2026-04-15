#!/usr/bin/env bash
set -euo pipefail
#
# setup_dev_rules_autoupdate_macos.sh
#
# 与 agent-skills 同模式：
# 1. symlink ~/.cursor/rules/ 下的各 .mdc → 本仓库
# 2. symlink ~/.claude/commands/ 下的各 .md → 本仓库
# 3. 注册 macOS LaunchAgent 每小时自动 git pull
#
# 用法：
#   ./setup_dev_rules_autoupdate_macos.sh load [repo_dir]
#   ./setup_dev_rules_autoupdate_macos.sh unload

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
DEFAULT_REPO="${DEFAULT_REPO:-$SCRIPT_DIR}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-3600}"
JOB_LABEL="${JOB_LABEL:-local.dev-rules.sync}"
MAIN_BRANCH="${MAIN_BRANCH:-main}"
ACTION="load"

if [[ $# -ge 1 ]]; then
    case "$1" in
        load|unload) ACTION="$1"; shift ;;
    esac
fi

REPO_DIR="${1:-$DEFAULT_REPO}"
REPO_DIR="${REPO_DIR/#\~/$HOME}"
LOCAL_BIN_DIR="$HOME/.local/bin"
UPDATE_SCRIPT="$LOCAL_BIN_DIR/update-dev-rules.sh"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/${JOB_LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/dev-rules-sync"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This script must run on macOS (Darwin)." >&2
    exit 1
fi

GIT_CMD="$(command -v git)" || { echo "git is required but not found." >&2; exit 1; }
mkdir -p "$LOCAL_BIN_DIR" "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

if [[ "$ACTION" == "unload" ]]; then
    launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
    rm -f "$PLIST_PATH"
    rm -f "$UPDATE_SCRIPT"
    echo "Unloaded auto-update."
    echo "Removed: $PLIST_PATH"
    echo "Removed: $UPDATE_SCRIPT"
    echo "Kept repo and symlinks untouched."
    exit 0
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "Repository not found: $REPO_DIR" >&2
    echo "Expected a git repo at $DEFAULT_REPO (or pass custom path as arg)." >&2
    exit 1
fi

REPO_DIR="$(cd "$REPO_DIR" && pwd)"

# Run initial sync
echo "=== Running initial sync ==="
"$REPO_DIR/sync.sh"

# Create the update script (git pull + re-sync)
cat > "$UPDATE_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="$REPO_DIR"
MAIN_BRANCH="$MAIN_BRANCH"
GIT_CMD="$GIT_CMD"
LOG_PREFIX="[dev-rules-sync]"

if [[ ! -d "\$REPO_DIR/.git" ]]; then
    echo "\$LOG_PREFIX repo missing: \$REPO_DIR"
    exit 1
fi
if [[ "\$(\$GIT_CMD -C "\$REPO_DIR" rev-parse --abbrev-ref HEAD)" != "\$MAIN_BRANCH" ]]; then
    echo "\$LOG_PREFIX not on \$MAIN_BRANCH; skip auto-pull"
    exit 0
fi
if [[ -n "\$(\$GIT_CMD -C "\$REPO_DIR" status --porcelain)" ]]; then
    echo "\$LOG_PREFIX working tree dirty; skip auto-pull"
    exit 0
fi

BEFORE="\$(\$GIT_CMD -C "\$REPO_DIR" rev-parse HEAD)"
echo "\$LOG_PREFIX pulling latest from origin/\$MAIN_BRANCH"
\$GIT_CMD -C "\$REPO_DIR" pull --ff-only origin "\$MAIN_BRANCH"
AFTER="\$(\$GIT_CMD -C "\$REPO_DIR" rev-parse HEAD)"

if [[ "\$BEFORE" != "\$AFTER" ]]; then
    echo "\$LOG_PREFIX rules updated, re-syncing to registered projects"
    "\$REPO_DIR/sync.sh" --all
fi

echo "\$LOG_PREFIX done at \$(date '+%Y-%m-%d %H:%M:%S')"
EOF
chmod +x "$UPDATE_SCRIPT"

# Create LaunchAgent plist
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>$JOB_LABEL</string>
    <key>ProgramArguments</key>
    <array>
      <string>$UPDATE_SCRIPT</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StartInterval</key>
    <integer>$INTERVAL_SECONDS</integer>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/stderr.log</string>
  </dict>
</plist>
EOF

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"
"$UPDATE_SCRIPT" >> "$LOG_DIR/stdout.log" 2>> "$LOG_DIR/stderr.log" || true

echo ""
echo "=== Setup complete ==="
echo "Repo dir:  $REPO_DIR"
echo "Agent:     $JOB_LABEL"
echo "Interval:  ${INTERVAL_SECONDS}s (1 hour)"
echo "Plist:     $PLIST_PATH"
echo "Updater:   $UPDATE_SCRIPT"
echo "Logs:      $LOG_DIR/{stdout.log,stderr.log}"
echo ""
echo "Quick checks:"
echo "  launchctl list | grep '$JOB_LABEL'"
echo "  tail -n 50 '$LOG_DIR/stdout.log'"
echo ""
echo "To sync rules to a project:"
echo "  $REPO_DIR/sync.sh --register /path/to/project"
echo "  $REPO_DIR/sync.sh --all"
