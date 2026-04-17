#!/usr/bin/env bash
#
# dev-rules/sync.sh — 规则分发脚本
#
# 从 dev-rules/（单一事实来源）同步规则到所有消费端。
#
# 用法：
#   ./sync.sh                    # 同步到本地 home 目录（symlinks 指向 ~/Codes/dev-rules/）
#   ./sync.sh --local            # 从当前 submodule 同步到父项目的 .cursor/rules/（real copy）
#                                #   首次运行会自动 register 该项目，之后 --pull / --all 会带上它
#   ./sync.sh --push             # 在 submodule 中 push 远端 + 在 ~/Codes 拉取 + fan-out 到所有已注册项目
#                                #   这是「编辑 dev-rules → 全机生效」的标准入口
#   ./sync.sh --pull             # 从远端拉取 ~/Codes/dev-rules + fan-out（LaunchAgent / 跨机器同步用）
#   ./sync.sh --all              # 同步 home + 所有已注册项目（不联网）
#   ./sync.sh --project /path    # 同步规则到指定项目（real copy）
#   ./sync.sh --register /path   # 手动注册项目（通常 --local 已自动）
#   ./sync.sh --list             # 列出所有已注册项目
#   ./sync.sh --check            # 检测父项目 .cursor/rules/ 与 submodule 的 drift（CI 用，exit 1）
#   ./sync.sh --status           # 查看当前同步状态（含 LaunchAgent 是否激活）
#
# 架构说明：
#
#   github.com/youxuanxue/dev-rules         ← 远端真相（remote source of truth）
#        │ git push（手动） / git pull（LaunchAgent + --push 触发）
#   ~/Codes/dev-rules/                      ← 本机规范副本（local canonical mirror）
#   ├── rules/*.mdc                            symlink 与 fan-out 都从这里出发
#   ├── commands/*.md
#   └── global/CLAUDE.md
#        │
#        ├──→ ~/.cursor/rules/*.mdc          本地 Cursor 交互式会话（symlink）
#        ├──→ ~/.claude/commands/*           本地 Claude Code 自定义命令（symlink）
#        ├──→ ~/.claude/CLAUDE.md            全局工作宪法（symlink）
#        └──→ 各项目/.cursor/rules/*.mdc     云端 Agent 可读（real copy, git tracked）
#
#   为什么 home 用 symlink，项目用 real copy？
#     - home 同机即时生效，无需重 sync
#     - 项目要 git track + 云端 VM 克隆时不能依赖 home 目录
#
#   两个失效模式 + 各自的兜底：
#     - 本机修改 + push  →  --push wrapper 一步搞定（pull ~/Codes + fan-out）
#     - 跨机器有人 push   →  LaunchAgent 每 30 min 跑 --pull（兜底）

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RULES_DIR="$SCRIPT_DIR/rules"
COMMANDS_DIR="$SCRIPT_DIR/commands"
GLOBAL_DIR="$SCRIPT_DIR/global"

# Canonical local mirror (the symlink target). symlinks must always point here,
# never into a project's submodule (which would couple home rules to a project).
HOME_CANONICAL="${DEV_RULES_HOME:-$HOME/Codes/dev-rules}"
HOME_RULES_DIR="$HOME_CANONICAL/rules"
HOME_COMMANDS_DIR="$HOME_CANONICAL/commands"
HOME_GLOBAL_DIR="$HOME_CANONICAL/global"

CURSOR_HOME="$HOME/.cursor/rules"
CLAUDE_COMMANDS="$HOME/.claude/commands"
CLAUDE_GLOBAL_MD="$HOME/.claude/CLAUDE.md"

LAUNCH_AGENT_LABEL="local.dev-rules.sync"
LAUNCH_AGENT_PLIST="$HOME/Library/LaunchAgents/${LAUNCH_AGENT_LABEL}.plist"

# Registered projects file lives in ~/Codes/dev-rules/ (so all submodule copies share it)
PROJECTS_FILE="$HOME_CANONICAL/.registered-projects"

sync_to_home() {
    if [ ! -d "$HOME_CANONICAL" ]; then
        echo "  WARN: $HOME_CANONICAL not found — skipping home sync"
        echo "         (clone first: git clone git@github.com:youxuanxue/dev-rules.git $HOME_CANONICAL)"
        return 0
    fi

    echo "=== Syncing to ~/.cursor/rules/ (symlinks → $HOME_RULES_DIR) ==="
    mkdir -p "$CURSOR_HOME"
    for rule in "$HOME_RULES_DIR"/*.mdc; do
        [ -f "$rule" ] || continue
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
    echo "=== Syncing to ~/.claude/commands/ (symlinks → $HOME_COMMANDS_DIR) ==="
    mkdir -p "$CLAUDE_COMMANDS"
    for cmd in "$HOME_COMMANDS_DIR"/*.md; do
        [ -f "$cmd" ] || continue
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

    echo ""
    echo "=== Syncing to ~/.claude/CLAUDE.md (symlink → $HOME_GLOBAL_DIR/CLAUDE.md) ==="
    local global_src="$HOME_GLOBAL_DIR/CLAUDE.md"
    if [ ! -f "$global_src" ]; then
        echo "  WARN: $global_src not found, skipping"
    elif [ -L "$CLAUDE_GLOBAL_MD" ] && [ "$(readlink "$CLAUDE_GLOBAL_MD")" = "$global_src" ]; then
        echo "  ok: CLAUDE.md → $global_src"
    else
        if [ -f "$CLAUDE_GLOBAL_MD" ] && [ ! -L "$CLAUDE_GLOBAL_MD" ]; then
            local backup="$CLAUDE_GLOBAL_MD.bak.$(date +%Y%m%d%H%M%S)"
            mv "$CLAUDE_GLOBAL_MD" "$backup"
            echo "  backup: $CLAUDE_GLOBAL_MD → $backup"
        fi
        ln -sf "$global_src" "$CLAUDE_GLOBAL_MD"
        echo "  linked: CLAUDE.md → $global_src"
    fi
}

sync_to_project() {
    local project_dir="$1"
    local source_rules_dir="${2:-$RULES_DIR}"

    if [ ! -d "$project_dir" ]; then
        echo "  SKIP (not found): $project_dir"
        return 0
    fi
    if [ ! -d "$source_rules_dir" ]; then
        echo "  SKIP (source missing): $source_rules_dir"
        return 0
    fi

    local target_rules="$project_dir/.cursor/rules"
    mkdir -p "$target_rules"

    local changed=0
    for rule in "$source_rules_dir"/*.mdc; do
        [ -f "$rule" ] || continue
        local basename
        basename="$(basename "$rule")"
        local target="$target_rules/$basename"

        if [ -f "$target" ] && diff -q "$rule" "$target" > /dev/null 2>&1; then
            :
        else
            cp "$rule" "$target"
            echo "  copied: $basename → $(basename "$project_dir")"
            changed=1
        fi
    done

    if [ "$changed" -eq 0 ]; then
        echo "  ok: $(basename "$project_dir") (all rules up to date)"
    fi
}

sync_local() {
    local parent_dir
    parent_dir="$(cd "$SCRIPT_DIR/.." && pwd)"

    if [ ! -f "$parent_dir/.gitmodules" ] || ! grep -q "dev-rules" "$parent_dir/.gitmodules" 2>/dev/null; then
        echo "Warning: dev-rules does not appear to be a submodule in $parent_dir"
        echo "  (continuing anyway)"
    fi

    echo "=== Syncing to parent project: $(basename "$parent_dir")/.cursor/rules/ (real copies) ==="
    sync_to_project "$parent_dir"

    # Auto-register: future --pull / --push fan-out will include this project
    auto_register "$parent_dir"
}

auto_register() {
    local project_dir="$1"
    if [ ! -d "$HOME_CANONICAL" ]; then
        return 0
    fi
    # If we can't even write to the projects file (sandbox / permissions), skip silently.
    # This happens in restricted execution contexts; non-fatal.
    if ! mkdir -p "$(dirname "$PROJECTS_FILE")" 2>/dev/null; then return 0; fi
    if ! touch "$PROJECTS_FILE" 2>/dev/null; then return 0; fi
    if ! grep -qxF "$project_dir" "$PROJECTS_FILE" 2>/dev/null; then
        if echo "$project_dir" >> "$PROJECTS_FILE" 2>/dev/null; then
            echo "  auto-registered: $project_dir"
        fi
    fi
}

# --push: 编辑 dev-rules 后的"全机生效"标准入口。
#  1) 在 SCRIPT_DIR（通常是项目内 submodule）执行 git push
#  2) 在 ~/Codes/dev-rules 执行 git pull --ff-only
#  3) 重刷所有 home symlinks + fan-out 到所有已注册项目
# 这条命令把 git push、本机镜像更新、跨项目同步合成一个原子动作。
sync_push() {
    echo "=== [1/3] Pushing submodule changes from $SCRIPT_DIR ==="
    if ! git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree > /dev/null 2>&1; then
        echo "  FAIL: $SCRIPT_DIR is not a git working tree"
        exit 1
    fi
    local branch
    branch="$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD)"
    if ! git -C "$SCRIPT_DIR" push origin "$branch"; then
        echo "  FAIL: git push failed; not proceeding to fan-out"
        exit 1
    fi
    local pushed_sha
    pushed_sha="$(git -C "$SCRIPT_DIR" rev-parse HEAD)"
    echo "  pushed $pushed_sha to origin/$branch"

    echo ""
    echo "=== [2/3] Pulling $HOME_CANONICAL ==="
    if [ ! -d "$HOME_CANONICAL/.git" ]; then
        echo "  WARN: $HOME_CANONICAL is not a git checkout — skipping mirror update"
        echo "         (clone first: git clone git@github.com:youxuanxue/dev-rules.git $HOME_CANONICAL)"
    elif [ "$(cd "$SCRIPT_DIR" && pwd)" = "$HOME_CANONICAL" ]; then
        echo "  same as SCRIPT_DIR — already up to date"
    else
        if ! git -C "$HOME_CANONICAL" diff --quiet || ! git -C "$HOME_CANONICAL" diff --cached --quiet; then
            echo "  WARN: $HOME_CANONICAL has uncommitted changes; aborting pull (resolve manually)"
        else
            git -C "$HOME_CANONICAL" fetch origin --quiet
            if ! git -C "$HOME_CANONICAL" merge --ff-only "origin/$branch" 2>&1 | sed 's/^/    /'; then
                echo "  FAIL: $HOME_CANONICAL cannot fast-forward to origin/$branch (diverged)"
                exit 1
            fi
            local mirror_sha
            mirror_sha="$(git -C "$HOME_CANONICAL" rev-parse HEAD)"
            echo "  mirror now at $mirror_sha"
        fi
    fi

    echo ""
    echo "=== [3/3] Fan-out: home + registered projects ==="
    sync_to_home
    sync_all_projects
    echo ""
    echo "Tip: each project listed above may now show modified .cursor/rules/* — review and commit per project."
}

# --pull: 跨机器同步路径。LaunchAgent 每 30 min 跑这个；用户也可手动救场。
#   1) ~/Codes/dev-rules pull --ff-only
#   2) 重刷 home symlinks + fan-out
sync_pull() {
    echo "=== [1/2] Pulling $HOME_CANONICAL ==="
    if [ ! -d "$HOME_CANONICAL/.git" ]; then
        echo "  FAIL: $HOME_CANONICAL is not a git checkout"
        exit 1
    fi
    if ! git -C "$HOME_CANONICAL" diff --quiet || ! git -C "$HOME_CANONICAL" diff --cached --quiet; then
        echo "  WARN: $HOME_CANONICAL has uncommitted changes; skipping pull"
    else
        git -C "$HOME_CANONICAL" fetch origin --quiet
        local local_sha remote_sha
        local_sha="$(git -C "$HOME_CANONICAL" rev-parse HEAD)"
        remote_sha="$(git -C "$HOME_CANONICAL" rev-parse "origin/$(git -C "$HOME_CANONICAL" rev-parse --abbrev-ref HEAD)")"
        if [ "$local_sha" = "$remote_sha" ]; then
            echo "  already at $local_sha"
        else
            git -C "$HOME_CANONICAL" merge --ff-only "$remote_sha" 2>&1 | sed 's/^/    /' || {
                echo "  FAIL: cannot fast-forward (diverged)"
                exit 1
            }
            echo "  $local_sha → $remote_sha"
        fi
    fi

    echo ""
    echo "=== [2/2] Fan-out: home + registered projects ==="
    sync_to_home
    sync_all_projects
}

check_project_drift() {
    local project_dir="$1"
    local target_rules="$project_dir/.cursor/rules"
    local drift=0

    if [ ! -d "$target_rules" ]; then
        echo "  MISSING: $project_dir/.cursor/rules/ does not exist (run --local to create)"
        return 1
    fi

    for rule in "$RULES_DIR"/*.mdc; do
        local basename
        basename="$(basename "$rule")"
        local target="$target_rules/$basename"

        if [ ! -f "$target" ]; then
            echo "  DRIFT: $basename missing in $(basename "$project_dir")/.cursor/rules/"
            drift=1
        elif ! diff -q "$rule" "$target" > /dev/null 2>&1; then
            echo "  DRIFT: $basename differs from submodule source"
            drift=1
        fi
    done

    for target in "$target_rules"/*.mdc; do
        [ -f "$target" ] || continue
        local basename
        basename="$(basename "$target")"
        if [ ! -f "$RULES_DIR/$basename" ]; then
            echo "  DRIFT: $basename exists in .cursor/rules/ but not in dev-rules/rules/ (orphan)"
            drift=1
        fi
    done

    return $drift
}

check_drift() {
    local parent_dir
    parent_dir="$(cd "$SCRIPT_DIR/.." && pwd)"

    echo "=== Checking drift: $(basename "$parent_dir")/.cursor/rules/ vs submodule ==="
    if check_project_drift "$parent_dir"; then
        echo "  ok: no drift"
        exit 0
    else
        echo ""
        echo "Drift detected. Run: ./dev-rules/sync.sh --local"
        exit 1
    fi
}

register_project() {
    local project_dir
    project_dir="$(cd "$1" && pwd)"

    if [ ! -d "$HOME_CANONICAL" ]; then
        echo "FAIL: $HOME_CANONICAL does not exist; clone the dev-rules mirror first"
        exit 1
    fi

    mkdir -p "$(dirname "$PROJECTS_FILE")"
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
    echo "=== Syncing to registered projects (real copies, source: $HOME_RULES_DIR) ==="
    if [ -f "$PROJECTS_FILE" ] && [ -s "$PROJECTS_FILE" ]; then
        while IFS= read -r project; do
            sync_to_project "$project" "$HOME_RULES_DIR"
        done < "$PROJECTS_FILE"
    else
        echo "  (no projects registered — first ./sync.sh --local in any project will auto-register it)"
    fi
}

print_status() {
    echo ""
    echo "=== Sync Status ==="
    echo ""
    echo "Local canonical mirror: $HOME_CANONICAL"
    if [ -d "$HOME_CANONICAL/.git" ]; then
        local mirror_sha submod_sha
        mirror_sha="$(git -C "$HOME_CANONICAL" rev-parse --short HEAD 2>/dev/null || echo '?')"
        submod_sha="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
        if [ "$mirror_sha" = "$submod_sha" ] || [ "$SCRIPT_DIR" = "$HOME_CANONICAL" ]; then
            echo "  ✓ mirror @ $mirror_sha (in sync with this submodule)"
        else
            echo "  ⚠ mirror @ $mirror_sha   submodule @ $submod_sha (run --pull to align home symlinks)"
        fi
    else
        echo "  ✗ not a git checkout (clone: git clone git@github.com:youxuanxue/dev-rules.git $HOME_CANONICAL)"
    fi
    echo ""
    echo "Rules in mirror:"
    for rule in "$HOME_RULES_DIR"/*.mdc; do
        [ -f "$rule" ] && echo "  $(basename "$rule")"
    done
    echo ""
    echo "Commands in mirror:"
    for cmd in "$HOME_COMMANDS_DIR"/*.md; do
        [ -f "$cmd" ] && echo "  $(basename "$cmd")"
    done
    echo ""
    echo "Home ~/.cursor/rules/ (must symlink → $HOME_RULES_DIR):"
    local any=0
    for rule in "$CURSOR_HOME"/*.mdc; do
        [ -e "$rule" ] || continue
        any=1
        if [ -L "$rule" ]; then
            local target
            target="$(readlink "$rule")"
            if [[ "$target" == "$HOME_RULES_DIR/"* ]]; then
                echo "  ✓ $(basename "$rule")"
            else
                echo "  ⚠ $(basename "$rule") → $target (not pointing to canonical mirror)"
            fi
        elif [ -f "$rule" ]; then
            echo "  ⚠ $(basename "$rule") (regular file, should be symlink)"
        fi
    done
    [ "$any" -eq 0 ] && echo "  (none — run sync.sh)"
    echo ""
    echo "Home ~/.claude/CLAUDE.md:"
    if [ -L "$CLAUDE_GLOBAL_MD" ]; then
        local target
        target="$(readlink "$CLAUDE_GLOBAL_MD")"
        if [ "$target" = "$HOME_GLOBAL_DIR/CLAUDE.md" ]; then
            echo "  ✓ → $target"
        else
            echo "  ⚠ → $target (not pointing to canonical mirror)"
        fi
    elif [ -f "$CLAUDE_GLOBAL_MD" ]; then
        echo "  ⚠ regular file (run sync.sh to convert to symlink)"
    else
        echo "  ✗ missing"
    fi
    echo ""
    echo "LaunchAgent ($LAUNCH_AGENT_LABEL):"
    if [ -f "$LAUNCH_AGENT_PLIST" ]; then
        if command -v launchctl > /dev/null 2>&1; then
            # buffer first to avoid SIGPIPE under pipefail
            agent_listing="$(launchctl list 2>/dev/null || true)"
            if printf '%s\n' "$agent_listing" | grep -qF "$LAUNCH_AGENT_LABEL"; then
                echo "  ✓ installed and loaded"
            else
                echo "  ⚠ plist exists but not loaded — run: launchctl load $LAUNCH_AGENT_PLIST"
            fi
        else
            echo "  ? launchctl unavailable, cannot verify load state"
        fi
    else
        echo "  ✗ not installed — run: bash $SCRIPT_DIR/templates/install-launchagent.sh"
    fi
    echo ""
    list_projects
}

case "${1:-}" in
    --all)
        sync_to_home
        sync_all_projects
        print_status
        ;;
    --local)
        sync_local
        ;;
    --push)
        sync_push
        ;;
    --pull)
        sync_pull
        ;;
    --check)
        check_drift
        ;;
    --project)
        [ -z "${2:-}" ] && { echo "Usage: $0 --project /path/to/project"; exit 1; }
        sync_to_project "$2" "$HOME_RULES_DIR"
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
        sed -n '2,30p' "$0" | sed 's|^#||; s|^ ||'
        ;;
    *)
        sync_to_home
        print_status
        ;;
esac

echo ""
echo "Done."
