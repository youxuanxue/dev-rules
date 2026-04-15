#!/usr/bin/env bash
#
# dev-rules/sync.sh — 规则分发脚本
#
# 从 dev-rules/（单一事实来源）同步规则到所有消费端。
#
# 用法：
#   ./sync.sh                    # 同步到本地 home 目录
#   ./sync.sh --project /path    # 同步规则到指定项目（real copy）
#   ./sync.sh --all              # 同步到本地 + 所有已注册项目
#   ./sync.sh --status           # 查看当前同步状态
#   ./sync.sh --register /path   # 注册一个项目到同步列表
#   ./sync.sh --list             # 列出所有已注册项目
#
# 架构说明：
#
#   ~/Codes/dev-rules/rules/*.mdc     ← 唯一编辑入口（SINGLE SOURCE OF TRUTH）
#        │
#        ├──→ ~/.cursor/rules/*.mdc         本地 Cursor 交互式会话（symlink）
#        ├──→ ~/.claude/commands/*           本地 Claude Code 自定义命令（symlink）
#        └──→ 各项目/.cursor/rules/*.mdc    云端 Agent 可读（real copy, git tracked）
#
#   为什么 home 目录用 symlink？ → 修改 dev-rules 后立即生效，无需重新 sync
#   为什么项目目录用 real copy？ → 云端 VM 克隆 repo 时拿不到 symlink 目标文件

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RULES_DIR="$SCRIPT_DIR/rules"
COMMANDS_DIR="$SCRIPT_DIR/commands"

CURSOR_HOME="$HOME/.cursor/rules"
CLAUDE_COMMANDS="$HOME/.claude/commands"

PROJECTS_FILE="$SCRIPT_DIR/.registered-projects"

sync_to_home() {
    echo "=== Syncing to ~/.cursor/rules/ (symlinks) ==="
    mkdir -p "$CURSOR_HOME"
    for rule in "$RULES_DIR"/*.mdc; do
        local basename
        basename="$(basename "$rule")"
        local target="$CURSOR_HOME/$basename"

        if [ -L "$target" ] && [ "$(readlink "$target")" = "$rule" ]; then
            echo "  ok: $basename"
        elif [ -L "$target" ] || [ -f "$target" ]; then
            [ -f "$target" ] && [ ! -L "$target" ] && mv "$target" "$target.bak.$(date +%Y%m%d%H%M%S)"
            ln -sf "$rule" "$target"
            echo "  updated: $basename"
        else
            ln -sf "$rule" "$target"
            echo "  created: $basename"
        fi
    done

    echo ""
    echo "=== Syncing to ~/.claude/commands/ (symlinks) ==="
    mkdir -p "$CLAUDE_COMMANDS"
    for cmd in "$COMMANDS_DIR"/*.md; do
        local basename
        basename="$(basename "$cmd")"
        local target="$CLAUDE_COMMANDS/$basename"
        if [ -L "$target" ] && [ "$(readlink "$target")" = "$cmd" ]; then
            echo "  ok: $basename"
        else
            ln -sf "$cmd" "$target"
            echo "  updated: $basename"
        fi
    done
}

sync_to_project() {
    local project_dir="$1"

    if [ ! -d "$project_dir" ]; then
        echo "  SKIP (not found): $project_dir"
        return
    fi

    local target_rules="$project_dir/.cursor/rules"
    mkdir -p "$target_rules"

    local changed=0
    for rule in "$RULES_DIR"/*.mdc; do
        local basename
        basename="$(basename "$rule")"
        local target="$target_rules/$basename"

        if [ -f "$target" ] && diff -q "$rule" "$target" > /dev/null 2>&1; then
            : # unchanged
        else
            cp "$rule" "$target"
            echo "  copied: $basename → $(basename "$project_dir")"
            changed=1
        fi
    done

    [ "$changed" -eq 0 ] && echo "  ok: $(basename "$project_dir") (all rules up to date)"
}

register_project() {
    local project_dir
    project_dir="$(cd "$1" && pwd)"

    touch "$PROJECTS_FILE"
    if grep -qxF "$project_dir" "$PROJECTS_FILE" 2>/dev/null; then
        echo "Already registered: $project_dir"
    else
        echo "$project_dir" >> "$PROJECTS_FILE"
        echo "Registered: $project_dir"
    fi
}

list_projects() {
    echo "=== Registered projects ==="
    if [ -f "$PROJECTS_FILE" ] && [ -s "$PROJECTS_FILE" ]; then
        while IFS= read -r project; do
            if [ -d "$project" ]; then
                echo "  ✓ $project"
            else
                echo "  ✗ $project (not found)"
            fi
        done < "$PROJECTS_FILE"
    else
        echo "  (none — use ./sync.sh --register /path/to/project)"
    fi
}

sync_all_projects() {
    echo ""
    echo "=== Syncing to registered projects (real copies) ==="
    if [ -f "$PROJECTS_FILE" ] && [ -s "$PROJECTS_FILE" ]; then
        while IFS= read -r project; do
            sync_to_project "$project"
        done < "$PROJECTS_FILE"
    else
        echo "  (no projects registered — use ./sync.sh --register /path/to/project)"
    fi
}

print_status() {
    echo ""
    echo "=== Sync Status ==="
    echo ""
    echo "Source of truth: $RULES_DIR/"
    echo ""
    echo "Rules:"
    for rule in "$RULES_DIR"/*.mdc; do
        echo "  $(basename "$rule")"
    done
    echo ""
    echo "Commands:"
    for cmd in "$COMMANDS_DIR"/*.md; do
        echo "  $(basename "$cmd")"
    done
    echo ""
    echo "Home ~/.cursor/rules/:"
    for rule in "$CURSOR_HOME"/*.mdc; do
        if [ -L "$rule" ]; then
            local target
            target="$(readlink "$rule")"
            if [[ "$target" == "$RULES_DIR/"* ]]; then
                echo "  ✓ $(basename "$rule")"
            else
                echo "  ⚠ $(basename "$rule") → $target (not pointing to dev-rules)"
            fi
        elif [ -f "$rule" ]; then
            echo "  ⚠ $(basename "$rule") (regular file, should be symlink)"
        fi
    done 2>/dev/null || echo "  (none)"
    echo ""
    list_projects
}

case "${1:-}" in
    --all)
        sync_to_home
        sync_all_projects
        print_status
        ;;
    --project)
        [ -z "${2:-}" ] && { echo "Usage: $0 --project /path/to/project"; exit 1; }
        sync_to_project "$2"
        ;;
    --register)
        [ -z "${2:-}" ] && { echo "Usage: $0 --register /path/to/project"; exit 1; }
        register_project "$2"
        ;;
    --list)
        list_projects
        ;;
    --status)
        print_status
        ;;
    --help|-h)
        echo "Usage:"
        echo "  $0                    Sync to home directory (symlinks)"
        echo "  $0 --project /path    Sync to a specific project (real copy)"
        echo "  $0 --all              Sync to home + all registered projects"
        echo "  $0 --register /path   Register a project for --all sync"
        echo "  $0 --list             List registered projects"
        echo "  $0 --status           Show current sync status"
        ;;
    *)
        sync_to_home
        print_status
        ;;
esac

echo ""
echo "Done."
