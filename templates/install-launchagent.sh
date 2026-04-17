#!/usr/bin/env bash
#
# dev-rules/templates/install-launchagent.sh — 把 LaunchAgent 真的装上
#
# 解决的问题：早期文档承诺"LaunchAgent 每小时 git pull + sync"，但实际谁都没装，
# 导致 ~/Codes/dev-rules 静默落后远端。这条脚本把"承诺"变"事实"。
#
# 用法：
#   bash dev-rules/templates/install-launchagent.sh           # 安装并加载
#   bash dev-rules/templates/install-launchagent.sh --uninstall  # 卸载
#   bash dev-rules/templates/install-launchagent.sh --check   # 仅检查是否已加载（exit 0/1）
#
# 仅在 macOS 上有意义。其他平台请用 systemd timer / cron。

set -euo pipefail

LABEL="local.dev-rules.sync"
HOME_CANONICAL="${DEV_RULES_HOME:-$HOME/Codes/dev-rules}"
PLIST_TARGET="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_TEMPLATE="$SCRIPT_DIR/launchagent.plist"

if [ "$(uname)" != "Darwin" ]; then
    echo "FAIL: this script is macOS-only (uname=$(uname))"
    echo "      use systemd-timer or cron on Linux instead"
    exit 2
fi

action="${1:-install}"

# Helper: returns 0 if the LaunchAgent is currently loaded.
# We materialize the list output first; piping `launchctl list` directly into
# `grep -q` causes SIGPIPE (exit 141), which `set -o pipefail` upgrades to a
# fatal error. This is the source of the classic "load returned 0 but agent
# not visible" false negative.
agent_loaded() {
    local listing
    listing="$(launchctl list 2>/dev/null || true)"
    printf '%s\n' "$listing" | grep -qF "$LABEL"
}

case "$action" in
    --check)
        if agent_loaded; then
            echo "ok: $LABEL is loaded"
            exit 0
        else
            echo "FAIL: $LABEL not loaded"
            exit 1
        fi
        ;;
    --uninstall)
        if [ -f "$PLIST_TARGET" ]; then
            launchctl unload "$PLIST_TARGET" 2>/dev/null || true
            rm -f "$PLIST_TARGET"
            echo "removed: $PLIST_TARGET"
        else
            echo "nothing to uninstall (no plist at $PLIST_TARGET)"
        fi
        exit 0
        ;;
    install|"")
        ;;
    *)
        echo "Usage: $0 [install|--check|--uninstall]"
        exit 2
        ;;
esac

if [ ! -d "$HOME_CANONICAL" ]; then
    echo "FAIL: $HOME_CANONICAL does not exist"
    echo "      clone the canonical mirror first:"
    echo "        git clone git@github.com:youxuanxue/dev-rules.git $HOME_CANONICAL"
    exit 1
fi

if [ ! -x "$HOME_CANONICAL/sync.sh" ]; then
    echo "FAIL: $HOME_CANONICAL/sync.sh missing or not executable"
    exit 1
fi

if [ ! -f "$PLIST_TEMPLATE" ]; then
    echo "FAIL: template not found: $PLIST_TEMPLATE"
    exit 1
fi

mkdir -p "$LOG_DIR"
mkdir -p "$(dirname "$PLIST_TARGET")"

# Render template by substituting {{HOME_CANONICAL}} and {{HOME}}
sed -e "s|{{HOME_CANONICAL}}|$HOME_CANONICAL|g" \
    -e "s|{{HOME}}|$HOME|g" \
    "$PLIST_TEMPLATE" > "$PLIST_TARGET"

# Validate the rendered plist before loading
if command -v plutil > /dev/null 2>&1; then
    if ! plutil -lint "$PLIST_TARGET" > /dev/null; then
        echo "FAIL: rendered plist failed plutil -lint"
        cat "$PLIST_TARGET"
        exit 1
    fi
fi

# Reload (unload then load) to pick up changes if it was previously installed
launchctl unload "$PLIST_TARGET" 2>/dev/null || true
launchctl load "$PLIST_TARGET"

# launchctl list can lag a moment after load; retry briefly
loaded=0
for _ in 1 2 3 4 5; do
    if agent_loaded; then loaded=1; break; fi
    sleep 0.3
done

if [ "$loaded" -eq 1 ]; then
    echo "ok: $LABEL installed and loaded"
    echo "    plist:    $PLIST_TARGET"
    echo "    interval: 1800s (30 min) + RunAtLoad"
    echo "    log:      $LOG_DIR/dev-rules-sync.log"
    echo ""
    echo "First run will execute now (RunAtLoad). Check log shortly:"
    echo "  tail -f $LOG_DIR/dev-rules-sync.log"
else
    echo "FAIL: launchctl load returned 0 but agent not visible in list"
    exit 1
fi
