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
#   ./sync.sh --push             # 在 submodule 中 push 远端 + 在 ~/Codes 拉取 + fan-out 到本机已落地的注册项目
#                                #   这是「编辑 dev-rules → 全机生效」的标准入口
#   ./sync.sh --pull             # 从远端拉取 ~/Codes/dev-rules + fan-out（LaunchAgent / 跨机器同步用）
#   ./sync.sh --all              # 同步 home + 本机已落地的注册项目（不联网）
#   ./sync.sh --project /path    # 同步规则到指定项目（real copy）
#   ./sync.sh --register /path   # 手动注册项目（通常 --local 已自动）
#   ./sync.sh --list             # 列出所有已注册项目（含本机是否落地）
#   ./sync.sh --check            # 检测 .cursor/rules/ drift（CI 用，exit 1）
#                                #   submodule 模式（项目内运行）：检查父项目 .cursor/rules/ vs 父项目 dev-rules/rules/
#                                #   canonical 模式（~/Codes/dev-rules/ 内运行）：遍历 .registered-projects 中
#                                #     有 .local-projects 映射的条目，与该项目自己 dev-rules/rules/（按其 submodule SHA）比较
#   ./sync.sh --status           # 查看当前同步状态（含 LaunchAgent 是否激活）
#
# 架构说明：
#
#   DEV_RULES_REMOTE_URL                  ← required remote URL for first clone/submodule setup
#        │ git push（手动） / git pull（LaunchAgent + --push 触发）
#   ~/Codes/dev-rules/                      ← 本机规范副本（local canonical mirror）
#   ├── rules/*.mdc                            symlink 与 fan-out 都从这里出发
#   ├── commands/*.md
#   ├── global/CLAUDE.md
#   ├── global/hooks/*                         Claude Code 全局 hooks（脚本）
#   ├── global/bin/*                           CLI launchers（claude-kiro 等；secret 留 ~/.claude/ 本地文件）
#        │
#        ├──→ ~/.cursor/rules/*.mdc          本地 Cursor 交互式会话（symlink）
#        ├──→ ~/.claude/commands/*           本地 Claude Code 自定义命令（symlink）
#        ├──→ ~/.claude/CLAUDE.md            全局工作宪法（symlink）
#        ├──→ ~/.claude/hooks/*              全局 Claude Code hooks（symlink）
#        ├──→ ~/.local/bin/*                 CLI launchers（symlink）
#        ├──→ ~/.codex/AGENTS.md + skills/*  Codex 消费端：同一宪法 + 逐个 skill（symlink）
#        ├──→ ~/.gemini/antigravity-cli/     Antigravity CLI 消费端：AGENTS.md 宪法 + skills/*（symlink）
#        └──→ 各项目/.cursor/rules/*.mdc     云端 Agent 可读（real copy, git tracked）
#
#   为什么 home 入口用 symlink，项目用 real copy？
#     - home 规则/命令同机即时生效，无需重 sync
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
HOME_HOOKS_DIR="$HOME_GLOBAL_DIR/hooks"
HOME_BIN_DIR="$HOME_GLOBAL_DIR/bin"

CURSOR_HOME="$HOME/.cursor/rules"
CLAUDE_COMMANDS="$HOME/.claude/commands"
CLAUDE_GLOBAL_MD="$HOME/.claude/CLAUDE.md"
CLAUDE_HOOKS="$HOME/.claude/hooks"
LOCAL_BIN="$HOME/.local/bin"
# Skills single source of truth: authored/committed under .cursor/skills/.
# Claude Code loads them ONLY via a .claude/skills symlink (never a real copy —
# a copy forks the truth, which the convention forbids).
CURSOR_SKILLS="$HOME/.cursor/skills"
CLAUDE_SKILLS="$HOME/.claude/skills"
HOME_CURSOR_SKILLS_SRC="$HOME_CANONICAL/.cursor/skills"

# Codex CLI/app consumer. Codex reads ~/.codex/AGENTS.md (global instructions),
# scans ~/.codex/skills/<name>/SKILL.md (follows symlinks), and keeps its OWN
# command-approval policy in ~/.codex/rules/*.rules (Starlark) — which is NOT a
# behavioral-rules dir, so we never write there. Behavioral rules + the
# constitution reach Codex via AGENTS.md (home symlink + per-project block).
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
CODEX_AGENTS_MD="$CODEX_HOME_DIR/AGENTS.md"
CODEX_SKILLS="$CODEX_HOME_DIR/skills"
# Skill dir entries we must never treat as user skills (Codex-managed / noise).
CODEX_SKILL_RESERVED=".system codex-primary-runtime .DS_Store"
GEN_CODEX_AGENTS="$SCRIPT_DIR/scripts/gen_codex_agents.py"

# Antigravity CLI (Google `agy`, brew cask antigravity-cli) consumer. Antigravity
# 1.0.7 auto-discovers customizations from its Global Customizations Root (= the CLI
# app data dir, ~/.gemini/antigravity-cli): global rules from AGENTS.md, skills from
# skills/<name>/SKILL.md. Same model as Codex, so the SAME two additive links carry
# the single source: AGENTS.md → the constitution, skills/<name> → each agent-skill.
# Workspace-level rules reach it via the project-root AGENTS.md managed block (shared
# with Codex); workspace skills via <project>/.agents/skills (its Workspace Root).
ANTIGRAVITY_HOME_DIR="${ANTIGRAVITY_HOME:-$HOME/.gemini/antigravity-cli}"
ANTIGRAVITY_AGENTS_MD="$ANTIGRAVITY_HOME_DIR/AGENTS.md"
ANTIGRAVITY_SKILLS="$ANTIGRAVITY_HOME_DIR/skills"
# Source-side names link_each_skill must skip (noise only; Antigravity's own builtin
# skills live elsewhere and our additive links never touch unrelated dest entries).
ANTIGRAVITY_SKILL_RESERVED=".DS_Store"

LAUNCH_AGENT_LABEL="local.dev-rules.sync"
LAUNCH_AGENT_PLIST="$HOME/Library/LaunchAgents/${LAUNCH_AGENT_LABEL}.plist"

# Project registries live under the canonical mirror so submodule checkouts share them.
PROJECTS_FILE="$HOME_CANONICAL/.registered-projects"
LOCAL_PROJECTS_FILE="$HOME_CANONICAL/.local-projects"

# Registry helpers. Local fan-out uses existing paths from `.registered-projects` + `.local-projects`.

project_git_url() {
    git -C "$1" remote get-url origin 2>/dev/null
}

is_git_checkout() {
    git -C "$1" rev-parse --is-inside-work-tree > /dev/null 2>&1
}

local_path_for() {
    local url="$1"
    [ -f "$LOCAL_PROJECTS_FILE" ] || return 0
    awk -F'\t' -v u="$url" '!/^#/ && NF>=2 && $1 == u {print $2; exit}' "$LOCAL_PROJECTS_FILE"
}

write_local_mapping() {
    local url="$1" local_path="$2"
    [ -z "$url" ] && return 0
    mkdir -p "$(dirname "$LOCAL_PROJECTS_FILE")" 2>/dev/null || return 0
    if [ ! -f "$LOCAL_PROJECTS_FILE" ]; then
        {
            echo "# dev-rules per-machine local materialization map"
            echo "# Format: <git_remote_url>\\t<absolute_local_path>"
            echo "# Auto-managed by sync.sh; .gitignore'd (do not commit)"
        } > "$LOCAL_PROJECTS_FILE" 2>/dev/null || return 0
    fi
    local tmp
    tmp="$(mktemp)" || return 0
    awk -F'\t' -v u="$url" -v p="$local_path" '
      BEGIN { OFS="\t" }
      /^#/ || NF==0 { print; next }
      $1 == u { if (!done) { print u, p; done=1 }; next }
      { print }
      END { if (!done) print u, p }
    ' "$LOCAL_PROJECTS_FILE" > "$tmp" && mv "$tmp" "$LOCAL_PROJECTS_FILE"
}

add_registered() {
    local name="$1" url="$2"
    [ -z "$url" ] && return 0
    mkdir -p "$(dirname "$PROJECTS_FILE")" 2>/dev/null || return 0
    if [ ! -f "$PROJECTS_FILE" ]; then
        {
            echo "# dev-rules cross-machine project registry (git-tracked)"
            echo "# Format: <name>\\t<git_remote_url>"
            echo "# Per-machine local paths live in .local-projects (gitignored)"
        } > "$PROJECTS_FILE" 2>/dev/null || return 0
    fi
    if awk -F'\t' -v u="$url" '!/^#/ && NF>=2 && $2 == u {found=1} END{exit !found}' "$PROJECTS_FILE" 2>/dev/null; then
        return 0
    fi
    if printf '%s\t%s\n' "$name" "$url" >> "$PROJECTS_FILE" 2>/dev/null; then
        echo "  registered: $name → $url"
    fi
}

# Yield TSV lines `name<TAB>url<TAB>local_path` for every project that should
# receive local fan-out on this machine. This is the union of:
#   1) .registered-projects entries with a matching .local-projects path
#   2) .local-projects entries, including local-only projects not registered
# Entries lacking an existing local directory are skipped. Duplicate paths are
# emitted once.
iter_local_projects() {
    local seen_file
    seen_file="$(mktemp)" || return 0

    if [ -f "$PROJECTS_FILE" ] && [ -s "$PROJECTS_FILE" ]; then
        local line name url local_path
        while IFS= read -r line; do
            case "$line" in ''|'#'*) continue ;; esac
            [[ "$line" == *$'\t'* ]] || continue
            name="${line%%$'\t'*}"
            url="${line#*$'\t'}"
            local_path="$(local_path_for "$url")"
            [ -n "$local_path" ] && [ -d "$local_path" ] || continue
            if ! grep -qxF "$local_path" "$seen_file" 2>/dev/null; then
                printf '%s\n' "$local_path" >> "$seen_file"
                printf '%s\t%s\t%s\n' "$name" "$url" "$local_path"
            fi
        done < "$PROJECTS_FILE"
    fi

    if [ -f "$LOCAL_PROJECTS_FILE" ] && [ -s "$LOCAL_PROJECTS_FILE" ]; then
        local line url local_path name
        while IFS= read -r line; do
            case "$line" in ''|'#'*) continue ;; esac
            [[ "$line" == *$'\t'* ]] || continue
            url="${line%%$'\t'*}"
            local_path="${line#*$'\t'}"
            [ -n "$local_path" ] && [ -d "$local_path" ] || continue
            if ! grep -qxF "$local_path" "$seen_file" 2>/dev/null; then
                printf '%s\n' "$local_path" >> "$seen_file"
                name="$(basename "$local_path")"
                printf '%s\t%s\t%s\n' "$name" "$url" "$local_path"
            fi
        done < "$LOCAL_PROJECTS_FILE"
    fi

    rm -f "$seen_file"
}

# Ensure a project-local skill consumer is a symlink → its .cursor/skills
# source. Project-local consumers remain whole-directory links because their
# source is not a mixed home registry.
link_project_skill_consumer_dir() {
    local link="$1" target="$2" guard="$3" label="$4"
    [ -d "$guard" ] || return 0
    if [ -L "$link" ] && [ "$(readlink "$link")" = "$target" ]; then
        echo "  ok: $label → $target"
        return 0
    fi
    if [ -e "$link" ] && [ ! -L "$link" ]; then
        local backup="$link.bak.$(date +%Y%m%d%H%M%S)"
        mv "$link" "$backup"
        echo "  backup: $link → $backup (was a real copy; single source is $guard)"
    fi
    ln -sfn "$target" "$link"
    echo "  linked: $label → $target"
}

# Preserve the established project-local Claude fan-out interface. Home-level
# consumers must use reconcile_owned_skill_links instead.
link_skills_dir() {
    link_project_skill_consumer_dir "$@"
}

# Return the configured agent-skills checkout behind the canonical mirror's
# .cursor/skills link. Consumers link directly to this source spelling rather
# than adopting entries from the mixed home registry.
home_cursor_skills_source() {
    [ -d "$HOME_CURSOR_SKILLS_SRC" ] || return 1
    if [ -L "$HOME_CURSOR_SKILLS_SRC" ]; then
        local target
        target="$(readlink "$HOME_CURSOR_SKILLS_SRC")"
        if [[ "$target" = /* ]]; then
            printf '%s\n' "$target"
        else
            (
                cd -L "$(dirname "$HOME_CURSOR_SKILLS_SRC")/$target"
                pwd -L
            )
        fi
    else
        printf '%s\n' "$HOME_CURSOR_SKILLS_SRC"
    fi
}

# ~/.cursor/skills is an additive registry, not a whole-directory symlink. A
# legacy symlink to the configured source is safely materialized; every other
# existing filesystem object is someone else's ownership and must be preserved.
ensure_additive_skill_root() {
    local root="$1"
    if [ ! -e "$root" ] && [ ! -L "$root" ]; then
        mkdir -p "$root" || {
            echo "FAIL: cursor-skills ownership conflict: unable to create $root" >&2
            return 1
        }
        return 0
    fi
    if [ -L "$root" ]; then
        if [ "$(readlink "$root")" != "$HOME_CURSOR_SKILLS_SRC" ]; then
            echo "FAIL: cursor-skills ownership conflict: $root is a foreign symlink → $(readlink "$root")" >&2
            return 1
        fi
        unlink "$root" || {
            echo "FAIL: cursor-skills ownership conflict: unable to materialize $root" >&2
            return 1
        }
        mkdir -p "$root" || {
            echo "FAIL: cursor-skills ownership conflict: unable to materialize $root" >&2
            return 1
        }
        echo "  materialized: cursor-skills registry from legacy symlink"
        return 0
    fi
    if [ -d "$root" ]; then
        return 0
    fi
    echo "FAIL: cursor-skills ownership conflict: $root is a real file" >&2
    return 1
}

# Consumer skill roots must be real directories. Cursor's one legacy-root
# exception is handled before this guard by ensure_additive_skill_root().
ensure_skill_destination_root() {
    local root="$1" label="$2"
    if [ ! -e "$root" ] && [ ! -L "$root" ]; then
        mkdir -p "$root" || {
            echo "FAIL: $label ownership conflict: unable to create destination root $root" >&2
            return 1
        }
        return 0
    fi
    if [ -L "$root" ]; then
        echo "FAIL: $label ownership conflict: destination root $root is a symlink → $(readlink "$root")" >&2
        return 1
    fi
    if [ ! -d "$root" ]; then
        echo "FAIL: $label ownership conflict: destination root $root is a real file" >&2
        return 1
    fi
}

skill_name_is_reserved() {
    local name="$1" reserved_list="$2" reserved
    for reserved in $reserved_list; do
        [ "$name" = "$reserved" ] && return 0
    done
    return 1
}

# Normalize an absolute path even when its leaf is dangling. Python's realpath
# resolves any existing symlink parents and lexically normalizes the remainder,
# so ownership checks cannot be fooled by source/../foreign spelling.
normalize_skill_path() {
    python3 -c 'import os, sys; print(os.path.realpath(os.path.abspath(sys.argv[1])))' "$1"
}

# Normalized source containment is an ownership test only. It remains safe for
# stale dangling links while preserving foreign links whose textual target
# merely shares a source prefix; desired links themselves must retain their
# direct configured source spelling.
skill_link_is_owned_by_source() {
    local source="$1" link="$2" target absolute_target normalized_target normalized_source
    [ -L "$link" ] || return 1
    target="$(readlink "$link")" || return 1
    if [[ "$target" = /* ]]; then
        absolute_target="$target"
    else
        absolute_target="$(dirname "$link")/$target"
    fi
    normalized_target="$(normalize_skill_path "$absolute_target")" || return 1
    normalized_source="$(normalize_skill_path "$source")" || return 1
    case "$normalized_target" in
        "$normalized_source"|"$normalized_source"/*) return 0 ;;
    esac
    return 1
}

# Additively reconcile only links owned by <source>. Foreign symlinks, files,
# and directories remain untouched; a desired same-name collision fails before
# changing any destination entry. Reserved names are always left to their owner.
reconcile_owned_skill_links() {
    local source="$1" destination="$2" label="$3" reserved_list="${4:-}"
    [ -d "$source" ] || return 0
    ensure_skill_destination_root "$destination" "$label" || return 1

    local entry name link
    for entry in "$source"/*; do
        [ -d "$entry" ] || continue
        [ -f "$entry/SKILL.md" ] || continue
        name="$(basename "$entry")"
        skill_name_is_reserved "$name" "$reserved_list" && continue
        link="$destination/$name"
        if [ -L "$link" ]; then
            if [ "$(readlink "$link")" = "$entry" ]; then
                continue
            fi
            if ! skill_link_is_owned_by_source "$source" "$link"; then
                echo "FAIL: $label ownership conflict: $link is a foreign symlink → $(readlink "$link")" >&2
                return 1
            fi
        elif [ -e "$link" ]; then
            echo "FAIL: $label ownership conflict: $link is a real path" >&2
            return 1
        fi
    done

    for entry in "$source"/*; do
        [ -d "$entry" ] || continue
        [ -f "$entry/SKILL.md" ] || continue
        name="$(basename "$entry")"
        skill_name_is_reserved "$name" "$reserved_list" && continue
        link="$destination/$name"
        if [ -L "$link" ] && [ "$(readlink "$link")" = "$entry" ]; then
            echo "  ok: $label/$name"
        else
            if [ -L "$link" ]; then
                unlink "$link" || {
                    echo "FAIL: $label ownership conflict: unable to replace $link" >&2
                    return 1
                }
            fi
            ln -s "$entry" "$link" || {
                echo "FAIL: $label ownership conflict: unable to create $link" >&2
                return 1
            }
            echo "  linked: $label/$name → $entry"
        fi
    done

    for link in "$destination"/*; do
        [ -L "$link" ] || continue
        name="$(basename "$link")"
        skill_name_is_reserved "$name" "$reserved_list" && continue
        entry="$source/$name"
        if [ -d "$entry" ] && [ -f "$entry/SKILL.md" ]; then
            continue
        fi
        if skill_link_is_owned_by_source "$source" "$link"; then
            unlink "$link" || {
                echo "FAIL: $label ownership conflict: unable to remove stale $link" >&2
                return 1
            }
            echo "  removed stale: $label/$name"
        fi
    done
}

sync_to_home() {
    if [ ! -d "$HOME_CANONICAL" ]; then
        echo "  WARN: $HOME_CANONICAL not found — skipping home sync"
        echo "         (set DEV_RULES_REMOTE_URL, then clone: git clone \"\$DEV_RULES_REMOTE_URL\" $HOME_CANONICAL)"
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
    for target in "$CLAUDE_COMMANDS"/*.md; do
        [ -L "$target" ] || continue
        local source
        source="$(readlink "$target")"
        case "$source" in
            "$HOME_COMMANDS_DIR"/*)
                if [ ! -e "$source" ]; then
                    rm -f "$target"
                    echo "  removed stale: $(basename "$target")"
                fi
                ;;
        esac
    done

    echo ""
    echo "=== Syncing to ~/.claude/hooks/ (symlinks → $HOME_HOOKS_DIR) ==="
    if [ ! -d "$HOME_HOOKS_DIR" ]; then
        echo "  (no hooks/ in canonical mirror, skipping)"
    else
        mkdir -p "$CLAUDE_HOOKS"
        for hook in "$HOME_HOOKS_DIR"/*; do
            [ -f "$hook" ] || continue
            local basename
            basename="$(basename "$hook")"
            local target="$CLAUDE_HOOKS/$basename"
            if [ -L "$target" ] && [ "$(readlink "$target")" = "$hook" ]; then
                echo "  ok: $basename"
            elif [ -L "$target" ] || [ -f "$target" ]; then
                [ -f "$target" ] && [ ! -L "$target" ] && mv "$target" "$target.bak.$(date +%Y%m%d%H%M%S)"
                ln -sf "$hook" "$target"
                echo "  updated: $basename"
            else
                ln -sf "$hook" "$target"
                echo "  created: $basename"
            fi
        done
    fi

    echo ""
    echo "=== Syncing to ~/.local/bin/ (launcher symlinks → $HOME_BIN_DIR) ==="
    if [ ! -d "$HOME_BIN_DIR" ]; then
        echo "  (no global/bin/ in canonical mirror, skipping)"
    else
        mkdir -p "$LOCAL_BIN"
        for bin_src in "$HOME_BIN_DIR"/*; do
            [ -f "$bin_src" ] || continue
            local basename
            basename="$(basename "$bin_src")"
            local target="$LOCAL_BIN/$basename"
            if [ -L "$target" ] && [ "$(readlink "$target")" = "$bin_src" ]; then
                echo "  ok: $basename"
            elif [ -L "$target" ] || [ -f "$target" ]; then
                [ -f "$target" ] && [ ! -L "$target" ] && mv "$target" "$target.bak.$(date +%Y%m%d%H%M%S)"
                ln -sf "$bin_src" "$target"
                echo "  updated: $basename"
            else
                ln -sf "$bin_src" "$target"
                echo "  created: $basename"
            fi
        done
    fi

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

    echo ""
    echo "=== Syncing to ~/.cursor/skills/ (additive registry from $HOME_CURSOR_SKILLS_SRC) ==="
    local home_skill_source
    if ! home_skill_source="$(home_cursor_skills_source)"; then
        echo "  WARN: $HOME_CURSOR_SKILLS_SRC not found — skipping global skill registry"
        echo "        (agent skills source is required for Claude/Codex/Antigravity global skills)"
    else
        mkdir -p "$(dirname "$CURSOR_SKILLS")"
        ensure_additive_skill_root "$CURSOR_SKILLS" || return 1
        reconcile_owned_skill_links "$home_skill_source" "$CURSOR_SKILLS" "cursor-skills" || return 1
    fi

    echo ""
    echo "=== Syncing to ~/.claude/skills/ (symlink → $CURSOR_SKILLS) ==="
    if [ ! -d "$CURSOR_SKILLS" ]; then
        echo "  (no ~/.cursor/skills, skipping — nothing for Claude Code to load)"
    else
        mkdir -p "$(dirname "$CLAUDE_SKILLS")"
        link_skills_dir "$CLAUDE_SKILLS" "$CURSOR_SKILLS" "$CURSOR_SKILLS" "skills"
    fi

    sync_to_codex_home || return 1
    sync_to_antigravity_home || return 1
}

# Codex CLI/app consumer (~/.codex). Two additive links, neither of which touches
# Codex-managed content (.system skills, codex-primary-runtime, default.rules):
#   1. ~/.codex/AGENTS.md  → global/CLAUDE.md  (same constitution as Claude/Cursor)
#   2. ~/.codex/skills/<name> → ~/.cursor/skills/<name>  (each agent-skill)
# Behavioral rules are NOT symlinked into ~/.codex/rules (that's Codex's command
# policy); they reach Codex through AGENTS.md (the constitution references them,
# and each project's AGENTS.md block indexes them).
sync_to_codex_home() {
    if [ ! -d "$CODEX_HOME_DIR" ]; then
        echo ""
        echo "=== ~/.codex/ (Codex consumer) ==="
        echo "  (no $CODEX_HOME_DIR — Codex not installed, skipping)"
        return 0
    fi

    echo ""
    echo "=== Syncing to ~/.codex/AGENTS.md (symlink → $HOME_GLOBAL_DIR/CLAUDE.md) ==="
    local global_src="$HOME_GLOBAL_DIR/CLAUDE.md"
    if [ ! -f "$global_src" ]; then
        echo "  WARN: $global_src not found, skipping"
    elif [ -L "$CODEX_AGENTS_MD" ] && [ "$(readlink "$CODEX_AGENTS_MD")" = "$global_src" ]; then
        echo "  ok: AGENTS.md → $global_src"
    else
        if [ -e "$CODEX_AGENTS_MD" ] && [ ! -L "$CODEX_AGENTS_MD" ]; then
            # Codex ships a 0-byte AGENTS.md; back up anything real before linking.
            if [ -s "$CODEX_AGENTS_MD" ]; then
                local backup="$CODEX_AGENTS_MD.bak.$(date +%Y%m%d%H%M%S)"
                mv "$CODEX_AGENTS_MD" "$backup"
                echo "  backup: $CODEX_AGENTS_MD → $backup"
            else
                rm -f "$CODEX_AGENTS_MD"
            fi
        fi
        ln -sfn "$global_src" "$CODEX_AGENTS_MD"
        echo "  linked: AGENTS.md → $global_src"
    fi

    echo ""
    echo "=== Syncing to ~/.codex/skills/ (owned per-skill symlinks) ==="
    local home_skill_source
    if ! home_skill_source="$(home_cursor_skills_source)"; then
        echo "  (agent skills source unavailable, skipping — nothing for Codex to load)"
    else
        reconcile_owned_skill_links "$home_skill_source" "$CODEX_SKILLS" "codex-skills" "$CODEX_SKILL_RESERVED" || return 1
    fi
}

# Antigravity CLI consumer (~/.gemini/antigravity-cli, its Global Customizations
# Root). Two additive links, mirroring Codex, neither touching Antigravity-managed
# content (builtin/, brain/, cache/, native skills):
#   1. .../AGENTS.md       → global/CLAUDE.md  (same constitution as Claude/Codex)
#   2. .../skills/<name>   → ~/.cursor/skills/<name>  (each agent-skill)
# Antigravity ignores .cursor/rules/*.mdc just like Codex; behavioral rules reach it
# through AGENTS.md (the constitution references them; each project's AGENTS.md block
# indexes them). No-op when Antigravity CLI isn't installed.
sync_to_antigravity_home() {
    if [ ! -d "$ANTIGRAVITY_HOME_DIR" ]; then
        echo ""
        echo "=== ~/.gemini/antigravity-cli/ (Antigravity CLI consumer) ==="
        echo "  (no $ANTIGRAVITY_HOME_DIR — Antigravity CLI not installed, skipping)"
        return 0
    fi

    echo ""
    echo "=== Syncing to ~/.gemini/antigravity-cli/AGENTS.md (symlink → $HOME_GLOBAL_DIR/CLAUDE.md) ==="
    local global_src="$HOME_GLOBAL_DIR/CLAUDE.md"
    if [ ! -f "$global_src" ]; then
        echo "  WARN: $global_src not found, skipping"
    elif [ -L "$ANTIGRAVITY_AGENTS_MD" ] && [ "$(readlink "$ANTIGRAVITY_AGENTS_MD")" = "$global_src" ]; then
        echo "  ok: AGENTS.md → $global_src"
    else
        if [ -e "$ANTIGRAVITY_AGENTS_MD" ] && [ ! -L "$ANTIGRAVITY_AGENTS_MD" ]; then
            # Back up anything real (non-empty) before linking; drop a 0-byte stub.
            if [ -s "$ANTIGRAVITY_AGENTS_MD" ]; then
                local backup="$ANTIGRAVITY_AGENTS_MD.bak.$(date +%Y%m%d%H%M%S)"
                mv "$ANTIGRAVITY_AGENTS_MD" "$backup"
                echo "  backup: $ANTIGRAVITY_AGENTS_MD → $backup"
            else
                rm -f "$ANTIGRAVITY_AGENTS_MD"
            fi
        fi
        ln -sfn "$global_src" "$ANTIGRAVITY_AGENTS_MD"
        echo "  linked: AGENTS.md → $global_src"
    fi

    echo ""
    echo "=== Syncing to ~/.gemini/antigravity-cli/skills/ (owned per-skill symlinks) ==="
    local home_skill_source
    if ! home_skill_source="$(home_cursor_skills_source)"; then
        echo "  (agent skills source unavailable, skipping — nothing for Antigravity to load)"
    else
        reconcile_owned_skill_links "$home_skill_source" "$ANTIGRAVITY_SKILLS" "antigravity-skills" "$ANTIGRAVITY_SKILL_RESERVED" || return 1
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

    # Skills fan-out: a project that keeps skills under .cursor/skills/ needs a
    # .claude/skills symlink for Claude Code to load them (Cursor reads .cursor,
    # Claude Code reads .claude). Relative target so the link is portable across
    # clones/machines and commits cleanly. No-op when the project has no skills.
    if [ -d "$project_dir/.cursor/skills" ]; then
        mkdir -p "$project_dir/.claude"
        link_skills_dir "$project_dir/.claude/skills" "../.cursor/skills" \
            "$project_dir/.cursor/skills" "$(basename "$project_dir")/.claude/skills"
    fi

    # Codex consumer (project layer). Codex reads <repo>/AGENTS.md as project
    # instructions; it ignores .cursor/rules/*.mdc and has no behavioral-rules
    # dir, so dev-rules capabilities are injected as a generated managed block
    # in AGENTS.md (constitution + rule index + skill index + commands).
    if [ -f "$GEN_CODEX_AGENTS" ]; then
        if python3 "$GEN_CODEX_AGENTS" --project "$project_dir"; then
            :
        else
            echo "  WARN: gen_codex_agents.py failed for $(basename "$project_dir")"
        fi
    fi
    # Codex also reads project-level .codex/skills/ — mirror the .claude/skills
    # pattern so Codex loads the same skills natively. No-op without skills.
    if [ -d "$project_dir/.cursor/skills" ]; then
        mkdir -p "$project_dir/.codex"
        link_project_skill_consumer_dir "$project_dir/.codex/skills" "../.cursor/skills" \
            "$project_dir/.cursor/skills" "$(basename "$project_dir")/.codex/skills"
    fi

    # Antigravity CLI reads project-level .agents/skills/ (its Workspace
    # Customizations Root). Same .claude/skills pattern. Workspace RULES need no
    # extra link — Antigravity reads the project-root AGENTS.md managed block
    # (generated above), the same file Codex consumes. No-op without skills.
    if [ -d "$project_dir/.cursor/skills" ]; then
        mkdir -p "$project_dir/.agents"
        link_project_skill_consumer_dir "$project_dir/.agents/skills" "../.cursor/skills" \
            "$project_dir/.cursor/skills" "$(basename "$project_dir")/.agents/skills"
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
    [ -d "$HOME_CANONICAL" ] || return 0
    local url
    url="$(project_git_url "$project_dir")"
    if [ -z "$url" ]; then
        echo "  note: $(basename "$project_dir") has no git remote 'origin'; skipping cross-machine registration"
        return 0
    fi
    add_registered "$(basename "$project_dir")" "$url" "$project_dir"
    write_local_mapping "$url" "$project_dir"
}

# --push: 编辑 dev-rules 后的"全机生效"标准入口。
#  1) 在 SCRIPT_DIR（通常是项目内 submodule）执行 git push
#  2) 在 ~/Codes/dev-rules 执行 git pull --ff-only
#  3) 重刷所有 home symlinks + fan-out 到本机已落地的注册项目
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
    if ! is_git_checkout "$HOME_CANONICAL"; then
        echo "  WARN: $HOME_CANONICAL is not a git checkout — skipping mirror update"
        echo "         (set DEV_RULES_REMOTE_URL, then clone: git clone \"\$DEV_RULES_REMOTE_URL\" $HOME_CANONICAL)"
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
    sync_to_home || exit $?
    sync_all_projects
    echo ""
    echo "Tip: each project listed above may now show modified .cursor/rules/* — review and commit per project."
}

# --pull: 跨机器同步路径。LaunchAgent 每 30 min 跑这个；用户也可手动救场。
#   1) ~/Codes/dev-rules pull --ff-only
#   2) 重刷 home symlinks + fan-out
sync_pull() {
    echo "=== [1/2] Pulling $HOME_CANONICAL ==="
    if ! is_git_checkout "$HOME_CANONICAL"; then
        echo "  FAIL: $HOME_CANONICAL is not a git checkout"
        exit 1
    fi
    local branch
    branch="$(git -C "$HOME_CANONICAL" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    if [ "$branch" != "main" ]; then
        echo "  FAIL: --pull requires canonical branch main (current: ${branch:-unknown})"
        echo "        refusing to fan-out rules from a feature or detached checkout"
        exit 1
    fi
    if ! git -C "$HOME_CANONICAL" diff --quiet || ! git -C "$HOME_CANONICAL" diff --cached --quiet; then
        echo "  WARN: $HOME_CANONICAL has uncommitted changes; skipping pull"
    else
        git -C "$HOME_CANONICAL" fetch origin --quiet
        local local_sha remote_sha
        local_sha="$(git -C "$HOME_CANONICAL" rev-parse HEAD)"
        remote_sha="$(git -C "$HOME_CANONICAL" rev-parse origin/main)"
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
    sync_to_home || exit $?
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

    # Source of truth = project's OWN dev-rules submodule (locked to its SHA),
    # not the canonical mirror. Each project legitimately versions its rules
    # by submodule SHA; canonical advancing beyond a project is normal.
    # Falls back to $RULES_DIR (this script's own rules) only when the project
    # has no submodule (rare; typically a non-submodule project that copied rules).
    local source_rules="$project_dir/dev-rules/rules"
    if [ ! -d "$source_rules" ]; then
        source_rules="$RULES_DIR"
        echo "  note: $project_dir has no dev-rules/ submodule; comparing against canonical mirror"
    fi

    for rule in "$source_rules"/*.mdc; do
        [ -f "$rule" ] || continue
        local basename
        basename="$(basename "$rule")"
        local target="$target_rules/$basename"

        if [ ! -f "$target" ]; then
            echo "  DRIFT: $basename missing in $(basename "$project_dir")/.cursor/rules/"
            drift=1
        elif ! diff -q "$rule" "$target" > /dev/null 2>&1; then
            echo "  DRIFT: $basename differs from project's dev-rules/rules/ source"
            drift=1
        fi
    done

    for target in "$target_rules"/*.mdc; do
        [ -f "$target" ] || continue
        local basename
        basename="$(basename "$target")"
        if [ ! -f "$source_rules/$basename" ]; then
            echo "  DRIFT: $basename exists in .cursor/rules/ but not in $source_rules/ (orphan)"
            drift=1
        fi
    done

    # Codex AGENTS.md managed block drift — OPT-IN: only check a project that
    # has already been onboarded to Codex (its AGENTS.md carries the block).
    # Projects not yet synced for Codex are not false-flagged. Runs the
    # project's OWN dev-rules generator (locked to its submodule SHA) so the
    # check matches what that project would regenerate; falls back to this copy.
    if [ -f "$project_dir/AGENTS.md" ] && \
       grep -q 'dev-rules:codex BEGIN' "$project_dir/AGENTS.md" 2>/dev/null; then
        local gen="$project_dir/dev-rules/scripts/gen_codex_agents.py"
        [ -f "$gen" ] || gen="$GEN_CODEX_AGENTS"
        if [ -f "$gen" ]; then
            if ! python3 "$gen" --project "$project_dir" --check > /tmp/dev-rules-codex-block.log 2>&1; then
                sed 's/^/  /' /tmp/dev-rules-codex-block.log
                drift=1
            fi
        fi
    fi

    return $drift
}

# Check command symlinks managed by dev-rules without policing user commands.
# A deleted canonical command must not survive as a dangling Claude entry.
check_home_commands_drift() {
    HOME_COMMANDS_DRIFT=0
    local command_src basename target actual
    for command_src in "$HOME_COMMANDS_DIR"/*.md; do
        [ -f "$command_src" ] || continue
        basename="$(basename "$command_src")"
        target="$CLAUDE_COMMANDS/$basename"
        if [ ! -L "$target" ]; then
            echo "  ✗ MISSING OR REAL FILE: ~/.claude/commands/$basename"
            HOME_COMMANDS_DRIFT=$((HOME_COMMANDS_DRIFT + 1))
        else
            actual="$(readlink "$target")"
            if [ "$actual" != "$command_src" ]; then
                echo "  ✗ WRONG TARGET: ~/.claude/commands/$basename → $actual (expected → $command_src)"
                HOME_COMMANDS_DRIFT=$((HOME_COMMANDS_DRIFT + 1))
            fi
        fi
    done
    for target in "$CLAUDE_COMMANDS"/*.md; do
        [ -L "$target" ] || continue
        actual="$(readlink "$target")"
        case "$actual" in
            "$HOME_COMMANDS_DIR"/*)
                if [ ! -e "$actual" ]; then
                    echo "  ✗ STALE: ~/.claude/commands/$(basename "$target") → $actual"
                    HOME_COMMANDS_DRIFT=$((HOME_COMMANDS_DRIFT + 1))
                fi
                ;;
        esac
    done
}

# Check that each file in $HOME_HOOKS_DIR has a correct symlink in $CLAUDE_HOOKS.
# Drift cases (each → +1 to HOME_HOOKS_DRIFT):
#   - missing: no entry in $CLAUDE_HOOKS for a canonical hook
#   - regular file: entry exists but isn't a symlink (someone edited live, or
#     a tool unlinked-then-wrote, severing version control)
#   - wrong target: symlink exists but points elsewhere
# User-only hooks in $CLAUDE_HOOKS (no counterpart in canonical) are ignored —
# this check enforces convention only, doesn't police what users add separately.
check_home_hooks_drift() {
    HOME_HOOKS_DRIFT=0
    [ -d "$HOME_HOOKS_DIR" ] || return 0
    local hook_src basename target actual
    for hook_src in "$HOME_HOOKS_DIR"/*; do
        [ -f "$hook_src" ] || continue
        basename="$(basename "$hook_src")"
        target="$CLAUDE_HOOKS/$basename"
        if [ ! -e "$target" ]; then
            echo "  ✗ MISSING: ~/.claude/hooks/$basename"
            HOME_HOOKS_DRIFT=$((HOME_HOOKS_DRIFT + 1))
        elif [ ! -L "$target" ]; then
            echo "  ✗ REGULAR FILE: ~/.claude/hooks/$basename (should be symlink to canonical mirror)"
            HOME_HOOKS_DRIFT=$((HOME_HOOKS_DRIFT + 1))
        else
            actual="$(readlink "$target")"
            if [ "$actual" != "$hook_src" ]; then
                echo "  ✗ WRONG TARGET: ~/.claude/hooks/$basename → $actual (expected → $hook_src)"
                HOME_HOOKS_DRIFT=$((HOME_HOOKS_DRIFT + 1))
            fi
        fi
    done
}

# Check that each launcher in $HOME_BIN_DIR has a correct symlink in $LOCAL_BIN.
# Same drift taxonomy as check_home_hooks_drift (missing / regular file / wrong
# target → +1 to HOME_BIN_DRIFT). User-only binaries in $LOCAL_BIN with no
# counterpart in canonical are ignored — convention only, not policing.
check_home_bin_drift() {
    HOME_BIN_DRIFT=0
    [ -d "$HOME_BIN_DIR" ] || return 0
    local bin_src basename target actual
    for bin_src in "$HOME_BIN_DIR"/*; do
        [ -f "$bin_src" ] || continue
        basename="$(basename "$bin_src")"
        target="$LOCAL_BIN/$basename"
        if [ ! -e "$target" ]; then
            echo "  ✗ MISSING: ~/.local/bin/$basename"
            HOME_BIN_DRIFT=$((HOME_BIN_DRIFT + 1))
        elif [ ! -L "$target" ]; then
            echo "  ✗ REGULAR FILE: ~/.local/bin/$basename (should be symlink to canonical mirror)"
            HOME_BIN_DRIFT=$((HOME_BIN_DRIFT + 1))
        else
            actual="$(readlink "$target")"
            if [ "$actual" != "$bin_src" ]; then
                echo "  ✗ WRONG TARGET: ~/.local/bin/$basename → $actual (expected → $bin_src)"
                HOME_BIN_DRIFT=$((HOME_BIN_DRIFT + 1))
            fi
        fi
    done
}

# Check that ~/.cursor/skills is a real additive registry containing only the
# dev-rules-owned links derived from the configured agent-skills source. Foreign
# entries are intentionally outside this drift contract.
check_home_cursor_skills_drift() {
    HOME_CURSOR_SKILLS_DRIFT=0

    local source
    if ! source="$(home_cursor_skills_source)"; then
        echo "  ✗ SOURCE MISSING: $HOME_CURSOR_SKILLS_SRC (agent skills source unavailable)"
        HOME_CURSOR_SKILLS_DRIFT=1
        return 0
    fi

    if [ ! -e "$CURSOR_SKILLS" ] && [ ! -L "$CURSOR_SKILLS" ]; then
        echo "  ✗ MISSING: ~/.cursor/skills (global skill registry missing)"
        HOME_CURSOR_SKILLS_DRIFT=1
        return 0
    elif [ -L "$CURSOR_SKILLS" ]; then
        echo "  ✗ SYMLINK: ~/.cursor/skills (must be a real additive registry)"
        HOME_CURSOR_SKILLS_DRIFT=1
        return 0
    elif [ ! -d "$CURSOR_SKILLS" ]; then
        echo "  ✗ REAL FILE: ~/.cursor/skills (must be a real directory)"
        HOME_CURSOR_SKILLS_DRIFT=1
        return 0
    fi

    local entry name link
    for entry in "$source"/*; do
        [ -d "$entry" ] || continue
        [ -f "$entry/SKILL.md" ] || continue
        name="$(basename "$entry")"
        link="$CURSOR_SKILLS/$name"
        if [ -L "$link" ] && [ "$(readlink "$link")" = "$entry" ]; then
            :
        elif [ ! -e "$link" ] && [ ! -L "$link" ]; then
            echo "  ✗ MISSING: ~/.cursor/skills/$name (dev-rules-owned skill link missing)"
            HOME_CURSOR_SKILLS_DRIFT=$((HOME_CURSOR_SKILLS_DRIFT + 1))
        else
            echo "  ✗ WRONG: ~/.cursor/skills/$name (not a dev-rules-owned symlink → $entry)"
            HOME_CURSOR_SKILLS_DRIFT=$((HOME_CURSOR_SKILLS_DRIFT + 1))
        fi
    done
}

# Check that ~/.claude/skills is the single-source symlink → ~/.cursor/skills,
# so Claude Code loads the same skills Cursor does. Only relevant when
# ~/.cursor/skills exists. Drift cases (each → HOME_SKILLS_DRIFT=1):
#   - missing: no .claude/skills, so Claude Code can't see any .cursor skill
#   - real dir/file: a forbidden duplicate that forks the single source
#   - wrong target: symlink points somewhere other than ~/.cursor/skills
check_home_skills_drift() {
    HOME_SKILLS_DRIFT=0
    [ -d "$CURSOR_SKILLS" ] || return 0
    if [ ! -e "$CLAUDE_SKILLS" ] && [ ! -L "$CLAUDE_SKILLS" ]; then
        echo "  ✗ MISSING: ~/.claude/skills (Claude Code can't load ~/.cursor/skills without it)"
        HOME_SKILLS_DRIFT=1
    elif [ ! -L "$CLAUDE_SKILLS" ]; then
        echo "  ✗ REAL DIR: ~/.claude/skills (forbidden duplicate; must be symlink → $CURSOR_SKILLS)"
        HOME_SKILLS_DRIFT=1
    else
        local actual
        actual="$(readlink "$CLAUDE_SKILLS")"
        if [ "$actual" != "$CURSOR_SKILLS" ]; then
            echo "  ✗ WRONG TARGET: ~/.claude/skills → $actual (expected → $CURSOR_SKILLS)"
            HOME_SKILLS_DRIFT=1
        fi
    fi
}

# Check the Codex consumer links in ~/.codex. Only relevant when Codex is
# installed (~/.codex exists). Each problem → +1 to HOME_CODEX_DRIFT:
#   - AGENTS.md not a symlink → global/CLAUDE.md (missing / real file / wrong target)
#   - a configured agent-skills source entry with no matching ~/.codex/skills/<name> symlink
# Codex-managed entries (.system, codex-primary-runtime, default.rules) are never
# inspected — this only enforces dev-rules' own additive links.
check_home_codex_drift() {
    HOME_CODEX_DRIFT=0
    [ -d "$CODEX_HOME_DIR" ] || return 0

    local global_src="$HOME_GLOBAL_DIR/CLAUDE.md"
    if [ ! -f "$global_src" ]; then
        :
    elif [ ! -L "$CODEX_AGENTS_MD" ]; then
        if [ -e "$CODEX_AGENTS_MD" ] && [ -s "$CODEX_AGENTS_MD" ]; then
            echo "  ✗ REAL FILE: ~/.codex/AGENTS.md (should be symlink → $global_src)"
        else
            echo "  ✗ MISSING: ~/.codex/AGENTS.md (Codex can't see the constitution)"
        fi
        HOME_CODEX_DRIFT=$((HOME_CODEX_DRIFT + 1))
    elif [ "$(readlink "$CODEX_AGENTS_MD")" != "$global_src" ]; then
        echo "  ✗ WRONG TARGET: ~/.codex/AGENTS.md → $(readlink "$CODEX_AGENTS_MD") (expected → $global_src)"
        HOME_CODEX_DRIFT=$((HOME_CODEX_DRIFT + 1))
    fi

    local source
    source="$(home_cursor_skills_source)" || return 0
    local entry name link reserved skip
    for entry in "$source"/*; do
        [ -d "$entry" ] || continue
        [ -f "$entry/SKILL.md" ] || continue
        name="$(basename "$entry")"
        skip=0
        for reserved in $CODEX_SKILL_RESERVED; do
            [ "$name" = "$reserved" ] && skip=1 && break
        done
        [ "$skip" -eq 1 ] && continue
        link="$CODEX_SKILLS/$name"
        if [ -L "$link" ] && [ "$(readlink "$link")" = "$entry" ]; then
            :
        elif [ ! -e "$link" ] && [ ! -L "$link" ]; then
            echo "  ✗ MISSING: ~/.codex/skills/$name (Codex won't load this skill)"
            HOME_CODEX_DRIFT=$((HOME_CODEX_DRIFT + 1))
        else
            echo "  ✗ WRONG: ~/.codex/skills/$name (not a symlink → $entry)"
            HOME_CODEX_DRIFT=$((HOME_CODEX_DRIFT + 1))
        fi
    done
}

# Check the Antigravity CLI consumer links in ~/.gemini/antigravity-cli. Mirror of
# check_home_codex_drift; only relevant when Antigravity CLI is installed. Each
# problem → +1 to HOME_ANTIGRAVITY_DRIFT:
#   - AGENTS.md not a symlink → global/CLAUDE.md (missing / real file / wrong target)
#   - a configured agent-skills source entry with no matching skills/<name> symlink
# Antigravity-managed content (builtin/, brain/, native skills) is never inspected.
check_home_antigravity_drift() {
    HOME_ANTIGRAVITY_DRIFT=0
    [ -d "$ANTIGRAVITY_HOME_DIR" ] || return 0

    local global_src="$HOME_GLOBAL_DIR/CLAUDE.md"
    if [ ! -f "$global_src" ]; then
        :
    elif [ ! -L "$ANTIGRAVITY_AGENTS_MD" ]; then
        if [ -e "$ANTIGRAVITY_AGENTS_MD" ] && [ -s "$ANTIGRAVITY_AGENTS_MD" ]; then
            echo "  ✗ REAL FILE: ~/.gemini/antigravity-cli/AGENTS.md (should be symlink → $global_src)"
        else
            echo "  ✗ MISSING: ~/.gemini/antigravity-cli/AGENTS.md (Antigravity can't see the constitution)"
        fi
        HOME_ANTIGRAVITY_DRIFT=$((HOME_ANTIGRAVITY_DRIFT + 1))
    elif [ "$(readlink "$ANTIGRAVITY_AGENTS_MD")" != "$global_src" ]; then
        echo "  ✗ WRONG TARGET: ~/.gemini/antigravity-cli/AGENTS.md → $(readlink "$ANTIGRAVITY_AGENTS_MD") (expected → $global_src)"
        HOME_ANTIGRAVITY_DRIFT=$((HOME_ANTIGRAVITY_DRIFT + 1))
    fi

    local source
    source="$(home_cursor_skills_source)" || return 0
    local entry name link reserved skip
    for entry in "$source"/*; do
        [ -d "$entry" ] || continue
        [ -f "$entry/SKILL.md" ] || continue
        name="$(basename "$entry")"
        skip=0
        for reserved in $ANTIGRAVITY_SKILL_RESERVED; do
            [ "$name" = "$reserved" ] && skip=1 && break
        done
        [ "$skip" -eq 1 ] && continue
        link="$ANTIGRAVITY_SKILLS/$name"
        if [ -L "$link" ] && [ "$(readlink "$link")" = "$entry" ]; then
            :
        elif [ ! -e "$link" ] && [ ! -L "$link" ]; then
            echo "  ✗ MISSING: ~/.gemini/antigravity-cli/skills/$name (Antigravity won't load this skill)"
            HOME_ANTIGRAVITY_DRIFT=$((HOME_ANTIGRAVITY_DRIFT + 1))
        else
            echo "  ✗ WRONG: ~/.gemini/antigravity-cli/skills/$name (not a symlink → $entry)"
            HOME_ANTIGRAVITY_DRIFT=$((HOME_ANTIGRAVITY_DRIFT + 1))
        fi
    done
}

check_drift() {
    # Two distinct invocation contexts:
    #   1. SCRIPT_DIR == HOME_CANONICAL → we are the canonical mirror at ~/Codes/dev-rules/.
    #      Parent (~/Codes/) is NOT a project; checking it would falsely report MISSING.
    #      Instead, iterate .registered-projects and check each consumer.
    #   2. SCRIPT_DIR is a submodule under some project → check that parent project.
    local parent_dir
    parent_dir="$(cd "$SCRIPT_DIR/.." && pwd)"

    if [ "$SCRIPT_DIR" = "$HOME_CANONICAL" ]; then
        # Canonical mirror mode: check every registered project that is materialized
        # on this machine. Projects registered cross-machine but without a local
        # clone are silently skipped (they'll be checked on whichever machine has them).
        local total_drift=0 checked=0 name url project
        while IFS=$'\t' read -r name url project; do
            echo "=== Checking drift: $name/.cursor/rules/ vs submodule ==="
            if check_project_drift "$project"; then
                echo "  ok: no drift"
            else
                total_drift=$((total_drift + 1))
            fi
            checked=$((checked + 1))
            echo ""
        done < <(iter_local_projects)

        # Home-command drift: expected command links plus deleted dev-rules
        # commands that still survive as dangling Claude entries.
        echo "=== Checking drift: ~/.claude/commands/ vs $HOME_COMMANDS_DIR ==="
        check_home_commands_drift
        if [ "$HOME_COMMANDS_DRIFT" -eq 0 ]; then
            echo "  ok: command symlinks healthy"
        else
            total_drift=$((total_drift + HOME_COMMANDS_DRIFT))
            echo "  $HOME_COMMANDS_DRIFT command link(s) drifted. Run: $SCRIPT_DIR/sync.sh"
        fi
        echo ""

        # Home-hooks drift: ~/.claude/hooks/ entries that should be symlinks to
        # $HOME_HOOKS_DIR but aren't (regular file, missing, wrong target).
        # User hooks not present in canonical mirror are NOT flagged.
        echo "=== Checking drift: ~/.claude/hooks/ vs $HOME_HOOKS_DIR ==="
        check_home_hooks_drift
        if [ "$HOME_HOOKS_DRIFT" -eq 0 ]; then
            echo "  ok: hooks symlinks healthy"
        else
            total_drift=$((total_drift + HOME_HOOKS_DRIFT))
            echo "  $HOME_HOOKS_DRIFT hook(s) drifted. Run: $SCRIPT_DIR/sync.sh"
        fi
        echo ""

        # Home-bin drift: ~/.local/bin/ launchers that should be symlinks to
        # $HOME_BIN_DIR but aren't. User binaries not in canonical are NOT flagged.
        echo "=== Checking drift: ~/.local/bin/ vs $HOME_BIN_DIR ==="
        check_home_bin_drift
        if [ "$HOME_BIN_DRIFT" -eq 0 ]; then
            echo "  ok: launcher symlinks healthy"
        else
            total_drift=$((total_drift + HOME_BIN_DRIFT))
            echo "  $HOME_BIN_DRIFT launcher(s) drifted. Run: $SCRIPT_DIR/sync.sh"
        fi
        echo ""

        # Home Cursor skills registry: only dev-rules-owned links derived from
        # the configured source are checked; foreign entries are preserved.
        echo "=== Checking drift: ~/.cursor/skills registry vs $HOME_CURSOR_SKILLS_SRC ==="
        check_home_cursor_skills_drift
        if [ "$HOME_CURSOR_SKILLS_DRIFT" -eq 0 ]; then
            echo "  ok: Cursor skill registry healthy"
        else
            total_drift=$((total_drift + HOME_CURSOR_SKILLS_DRIFT))
            echo "  Cursor skill registry drifted. Run: $SCRIPT_DIR/sync.sh"
        fi
        echo ""

        # Home-skills drift: ~/.claude/skills must be the single-source symlink
        # → ~/.cursor/skills (only checked when ~/.cursor/skills exists).
        echo "=== Checking drift: ~/.claude/skills vs $CURSOR_SKILLS ==="
        check_home_skills_drift
        if [ "$HOME_SKILLS_DRIFT" -eq 0 ]; then
            echo "  ok: skills symlink healthy"
        else
            total_drift=$((total_drift + HOME_SKILLS_DRIFT))
            echo "  skills symlink drifted. Run: $SCRIPT_DIR/sync.sh"
        fi
        echo ""

        # Codex consumer drift: ~/.codex/AGENTS.md + per-skill symlinks
        # (only when Codex is installed; Codex-managed entries untouched).
        echo "=== Checking drift: ~/.codex/ (Codex consumer) ==="
        check_home_codex_drift
        if [ "$HOME_CODEX_DRIFT" -eq 0 ]; then
            echo "  ok: Codex AGENTS.md + skills links healthy (or Codex not installed)"
        else
            total_drift=$((total_drift + HOME_CODEX_DRIFT))
            echo "  Codex links drifted. Run: $SCRIPT_DIR/sync.sh"
        fi
        echo ""

        # Antigravity CLI consumer drift: ~/.gemini/antigravity-cli/AGENTS.md +
        # per-skill symlinks (only when Antigravity is installed; managed entries untouched).
        echo "=== Checking drift: ~/.gemini/antigravity-cli/ (Antigravity CLI consumer) ==="
        check_home_antigravity_drift
        if [ "$HOME_ANTIGRAVITY_DRIFT" -eq 0 ]; then
            echo "  ok: Antigravity AGENTS.md + skills links healthy (or Antigravity not installed)"
        else
            total_drift=$((total_drift + HOME_ANTIGRAVITY_DRIFT))
            echo "  Antigravity links drifted. Run: $SCRIPT_DIR/sync.sh"
        fi
        echo ""

        if [ "$checked" -eq 0 ] && [ "$total_drift" -eq 0 ]; then
            echo "=== Checking drift: no materialized projects on this machine ==="
            echo "  ok: nothing to check (no .registered-projects entries have a matching .local-projects mapping)"
            exit 0
        fi

        if [ "$total_drift" -eq 0 ]; then
            echo "All $checked materialized project(s) and home links in sync."
            exit 0
        else
            echo "$total_drift drift item(s) across $checked project(s) + home links. Run: $SCRIPT_DIR/sync.sh --all"
            exit 1
        fi
    else
        # Submodule mode: check the parent project
        echo "=== Checking drift: $(basename "$parent_dir")/.cursor/rules/ vs submodule ==="
        if check_project_drift "$parent_dir"; then
            echo "  ok: no drift"
            exit 0
        else
            echo ""
            echo "Drift detected. Run: ./dev-rules/sync.sh --local"
            exit 1
        fi
    fi
}

register_project() {
    local project_dir
    project_dir="$(cd "$1" && pwd)"

    if [ ! -d "$HOME_CANONICAL" ]; then
        echo "FAIL: $HOME_CANONICAL does not exist; clone the dev-rules mirror first"
        exit 1
    fi

    local url
    url="$(project_git_url "$project_dir")"
    if [ -z "$url" ]; then
        echo "FAIL: $project_dir has no git remote 'origin' — cannot register cross-machine"
        echo "       (add a remote, or this project is local-only and shouldn't be registered)"
        exit 1
    fi

    local name
    name="$(basename "$project_dir")"
    if awk -F'\t' -v u="$url" '!/^#/ && NF>=2 && $2 == u {found=1} END{exit !found}' "$PROJECTS_FILE" 2>/dev/null; then
        echo "Already registered: $name → $url"
    else
        add_registered "$name" "$url"
    fi
    write_local_mapping "$url" "$project_dir"
    echo "Local mapping: $url → $project_dir"
}

list_projects() {
    echo "=== Registered projects (.registered-projects, cross-machine) ==="
    if [ ! -f "$PROJECTS_FILE" ] || [ ! -s "$PROJECTS_FILE" ]; then
        echo "  (none — use ./sync.sh --register /path/to/project, or run --local in any project)"
    else
        local line name url local_path any=0
        while IFS= read -r line; do
            case "$line" in ''|'#'*) continue ;; esac
            [[ "$line" == *$'\t'* ]] || continue
            any=1
            name="${line%%$'\t'*}"
            url="${line#*$'\t'}"
            local_path="$(local_path_for "$url")"
            if [ -n "$local_path" ] && [ -d "$local_path" ]; then
                echo "  ✓ $name  ($url)"
                echo "      local: $local_path"
            elif [ -n "$local_path" ]; then
                echo "  ✗ $name  ($url)"
                echo "      stale local path: $local_path (directory missing)"
            else
                echo "  ⊘ $name  ($url)"
                echo "      not cloned on this machine (run sync.sh --local in that clone to materialize)"
            fi
        done < "$PROJECTS_FILE"
        [ "$any" -eq 0 ] && echo "  (none — only comment lines)"
    fi

    echo ""
    echo "=== Materialized fan-out targets (.registered-projects ∪ .local-projects) ==="
    local materialized=0 target_name target_url target_path
    while IFS=$'\t' read -r target_name target_url target_path; do
        materialized=1
        echo "  ✓ $target_name  ($target_url)"
        echo "      local: $target_path"
    done < <(iter_local_projects)
    [ "$materialized" -eq 0 ] && echo "  (none on this machine)"
}

sync_all_projects() {
    echo ""
    echo "=== Syncing to materialized projects (registered + local-only, source: $HOME_RULES_DIR) ==="
    local any=0 name url project
    while IFS=$'\t' read -r name url project; do
        any=1
        sync_to_project "$project" "$HOME_RULES_DIR"
    done < <(iter_local_projects)
    if [ "$any" -eq 0 ]; then
        echo "  (no materialized projects on this machine)"
        echo "  (registered projects without a local clone, and local-only projects on other machines, are listed by --status / --list when materialized)"
    fi
}

print_status() {
    echo ""
    echo "=== Sync Status ==="
    echo ""
    echo "Local canonical mirror: $HOME_CANONICAL"
    if is_git_checkout "$HOME_CANONICAL"; then
        local mirror_sha submod_sha
        mirror_sha="$(git -C "$HOME_CANONICAL" rev-parse --short HEAD 2>/dev/null || echo '?')"
        submod_sha="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
        if [ "$mirror_sha" = "$submod_sha" ] || [ "$SCRIPT_DIR" = "$HOME_CANONICAL" ]; then
            echo "  ✓ mirror @ $mirror_sha (in sync with this submodule)"
        else
            echo "  ⚠ mirror @ $mirror_sha   submodule @ $submod_sha (run --pull to align home symlinks)"
        fi
    else
        echo "  ✗ not a git checkout (set DEV_RULES_REMOTE_URL, then clone: git clone \"\$DEV_RULES_REMOTE_URL\" $HOME_CANONICAL)"
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
    echo "Home ~/.cursor/skills (additive registry; dev-rules source: $HOME_CURSOR_SKILLS_SRC):"
    if [ -d "$CURSOR_SKILLS" ] && [ ! -L "$CURSOR_SKILLS" ]; then
        local owned_count=0 skill_source
        if skill_source="$(home_cursor_skills_source 2>/dev/null)"; then
            for skill in "$skill_source"/*; do
                [ -d "$skill" ] && [ -f "$skill/SKILL.md" ] && owned_count=$((owned_count + 1))
            done
        fi
        echo "  ✓ real registry ($owned_count dev-rules-owned skill link(s); foreign entries preserved)"
    elif [ -e "$CURSOR_SKILLS" ] || [ -L "$CURSOR_SKILLS" ]; then
        echo "  ⚠ not a real registry (run sync.sh to reconcile)"
    else
        echo "  ✗ missing (global skills will not load)"
    fi
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
    echo "Home ~/.claude/hooks/ (must symlink → $HOME_HOOKS_DIR):"
    if [ ! -d "$CLAUDE_HOOKS" ]; then
        echo "  ✗ directory missing — run sync.sh"
    else
        local hook_any=0
        for hook in "$CLAUDE_HOOKS"/*; do
            [ -e "$hook" ] || continue
            hook_any=1
            if [ -L "$hook" ]; then
                local target
                target="$(readlink "$hook")"
                if [[ "$target" == "$HOME_HOOKS_DIR/"* ]]; then
                    echo "  ✓ $(basename "$hook")"
                else
                    echo "  ⚠ $(basename "$hook") → $target (not pointing to canonical mirror)"
                fi
            elif [ -f "$hook" ]; then
                echo "  ⚠ $(basename "$hook") (regular file, should be symlink — mv to $HOME_HOOKS_DIR then re-sync)"
            fi
        done
        [ "$hook_any" -eq 0 ] && echo "  (none — run sync.sh)"
    fi
    echo ""
    echo "Home ~/.local/bin/ launchers (must symlink → $HOME_BIN_DIR):"
    if [ ! -d "$HOME_BIN_DIR" ]; then
        echo "  (no global/bin/ in canonical mirror)"
    else
        local bin_any=0
        for bin_src in "$HOME_BIN_DIR"/*; do
            [ -f "$bin_src" ] || continue
            bin_any=1
            local bin_name target_link
            bin_name="$(basename "$bin_src")"
            target_link="$LOCAL_BIN/$bin_name"
            if [ -L "$target_link" ] && [ "$(readlink "$target_link")" = "$bin_src" ]; then
                echo "  ✓ $bin_name"
            elif [ -e "$target_link" ]; then
                echo "  ⚠ $bin_name (not a symlink to canonical mirror — run sync.sh)"
            else
                echo "  ✗ $bin_name missing — run sync.sh"
            fi
        done
        [ "$bin_any" -eq 0 ] && echo "  (none in canonical mirror)"
    fi
    echo ""
    echo "Codex consumer (~/.codex, must mirror constitution + skills):"
    if [ ! -d "$CODEX_HOME_DIR" ]; then
        echo "  ⊘ not installed ($CODEX_HOME_DIR absent)"
    else
        if [ -L "$CODEX_AGENTS_MD" ] && [ "$(readlink "$CODEX_AGENTS_MD")" = "$HOME_GLOBAL_DIR/CLAUDE.md" ]; then
            echo "  ✓ AGENTS.md → $HOME_GLOBAL_DIR/CLAUDE.md"
        elif [ -L "$CODEX_AGENTS_MD" ]; then
            echo "  ⚠ AGENTS.md → $(readlink "$CODEX_AGENTS_MD") (not the constitution)"
        elif [ -e "$CODEX_AGENTS_MD" ]; then
            echo "  ⚠ AGENTS.md is a real file (run sync.sh to link)"
        else
            echo "  ✗ AGENTS.md missing (run sync.sh)"
        fi
        local codex_linked=0
        if [ -d "$CODEX_SKILLS" ]; then
            for s in "$CODEX_SKILLS"/*; do
                [ -L "$s" ] && codex_linked=$((codex_linked + 1))
            done
        fi
        echo "  skills: $codex_linked dev-rules symlink(s) in ~/.codex/skills (Codex-managed entries untouched)"
    fi
    echo ""
    echo "Antigravity CLI consumer (~/.gemini/antigravity-cli, must mirror constitution + skills):"
    if [ ! -d "$ANTIGRAVITY_HOME_DIR" ]; then
        echo "  ⊘ not installed ($ANTIGRAVITY_HOME_DIR absent)"
    else
        if [ -L "$ANTIGRAVITY_AGENTS_MD" ] && [ "$(readlink "$ANTIGRAVITY_AGENTS_MD")" = "$HOME_GLOBAL_DIR/CLAUDE.md" ]; then
            echo "  ✓ AGENTS.md → $HOME_GLOBAL_DIR/CLAUDE.md"
        elif [ -L "$ANTIGRAVITY_AGENTS_MD" ]; then
            echo "  ⚠ AGENTS.md → $(readlink "$ANTIGRAVITY_AGENTS_MD") (not the constitution)"
        elif [ -e "$ANTIGRAVITY_AGENTS_MD" ]; then
            echo "  ⚠ AGENTS.md is a real file (run sync.sh to link)"
        else
            echo "  ✗ AGENTS.md missing (run sync.sh)"
        fi
        local antigravity_linked=0
        if [ -d "$ANTIGRAVITY_SKILLS" ]; then
            for s in "$ANTIGRAVITY_SKILLS"/*; do
                [ -L "$s" ] && antigravity_linked=$((antigravity_linked + 1))
            done
        fi
        echo "  skills: $antigravity_linked dev-rules symlink(s) in ~/.gemini/antigravity-cli/skills (managed entries untouched)"
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
    echo "Registries:"
    echo "  cross-machine: $PROJECTS_FILE (git-tracked)"
    echo "  per-machine:   $LOCAL_PROJECTS_FILE (gitignored)"
    echo ""
    list_projects
}

case "${1:-}" in
    --all)
        sync_to_home || exit $?
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
    --check-preflight-drift)
        # Per-consumer: list dev-rules check_*.py scripts not wired into the
        # consumer's preflight. Informational (consumer may curate a subset);
        # use as a sweep tool when adding new check scripts to dev-rules.
        drift_script="$SCRIPT_DIR/scripts/check_preflight_stage_drift.py"
        if [ ! -f "$drift_script" ]; then
            echo "ERROR: $drift_script not found"
            exit 1
        fi
        total_consumers=0
        while IFS=$'\t' read -r name url project; do
            echo "=== Preflight drift: $name ==="
            python3 "$drift_script" --project "$project" --dev-rules-root "$SCRIPT_DIR"
            echo ""
            total_consumers=$((total_consumers + 1))
        done < <(iter_local_projects)
        if [ "$total_consumers" -eq 0 ]; then
            echo "=== Preflight drift: no materialized consumers ==="
        fi
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
        sync_to_home || exit $?
        print_status
        ;;
esac

echo ""
echo "Done."
