# Optional shell wrapper for wts. Source from ~/.zshrc:
#   source "$HOME/Codes/dev-rules/global/lib/wts.sh"
#
# Prefer ~/.local/bin/wts (installed by dev-rules/sync.sh). This function falls
# back to the canonical mirror when the symlink is missing.

wts() {
    local launcher="${WTS_LAUNCHER:-$HOME/.local/bin/wts}"
    if [[ ! -x "$launcher" ]]; then
        launcher="${DEV_RULES:-$HOME/Codes/dev-rules}/global/bin/wts"
    fi
    if [[ ! -x "$launcher" ]]; then
        echo "wts: launcher not found — run dev-rules/sync.sh" >&2
        return 1
    fi
    "$launcher" "$@"
}
