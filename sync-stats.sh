#!/usr/bin/env bash
#
# dev-rules/sync-stats.sh — 数值事实在散文文档中的强约束机制
#
# 解决「变更必伴漂移」：当 verify-rules.sh 段数从 6 → 7，所有写着「6 段」
# 的文档必须同步更新。靠人记忆 = 总会漏。本脚本把数值从「叙述」变为
# 「计算 + 占位」，让漂移变成机械可拦截的 exit code。
#
# 用法：
#   ./sync-stats.sh --update   # 重写所有文档中的 stat 块为最新值
#   ./sync-stats.sh --check    # 对比，发现漂移退出 1（CI/preflight 用）
#   ./sync-stats.sh --list     # 列出所有声明的 stats 及当前计算值
#
# 文档中的标记语法：
#   <!-- stat:NAME -->VALUE<!-- /stat -->
#
# 适用范围：
#   - dev-rules/.stats.json 中声明的所有 stats
#   - 扫描范围：调用方 git 仓库（superproject if submodule）下的所有
#     *.md / *.mdc 文件，自动跳过 .git / node_modules / dev-rules（避免
#     在子模块文档中误改父仓库声明的 stats）
#
# 设计原则：
#   - 单一事实来源（OPC 反脆弱）：每个数字只在 .stats.json 算一次
#   - 显式可读（Jobs 简洁）：HTML 注释让人一眼看出「这个数字由机器维护」
#   - 增量采纳：未标 stat 块的旧文档继续工作，按需迁移

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATS_FILE="$SCRIPT_DIR/.stats.json"

# Resolve target repo root (where to scan for stat blocks)
if [ -n "${SYNC_STATS_REPO:-}" ] && [ -d "$SYNC_STATS_REPO" ]; then
    REPO_ROOT="$SYNC_STATS_REPO"
elif git_top="$(git rev-parse --show-superproject-working-tree 2>/dev/null)" && [ -n "$git_top" ]; then
    REPO_ROOT="$git_top"
elif git_top="$(git rev-parse --show-toplevel 2>/dev/null)" && [ -n "$git_top" ]; then
    REPO_ROOT="$git_top"
else
    REPO_ROOT="$(pwd)"
fi

if [ ! -f "$STATS_FILE" ]; then
    echo "FAIL: $STATS_FILE not found"
    exit 2
fi

MODE="${1:-}"
case "$MODE" in
    --update|--check|--list) ;;
    *) echo "Usage: $0 --update | --check | --list"; exit 2 ;;
esac

# Extract stat names + compute commands from JSON without jq dependency
# Format expected: "name": { ... "compute": "shell command" ... }
extract_stats() {
    python3 -c "
import json, sys
with open('$STATS_FILE') as f:
    data = json.load(f)
for name, spec in data.get('stats', {}).items():
    print(f\"{name}\t{spec['compute']}\")
" 2>/dev/null || {
        echo "FAIL: cannot parse $STATS_FILE (need python3)" >&2
        exit 2
    }
}

# Compute one stat by running its shell expression from REPO_ROOT
compute_stat() {
    local cmd="$1"
    (cd "$REPO_ROOT" && bash -c "$cmd" 2>/dev/null | tr -d '\n')
}

# Print all current values (--list)
if [ "$MODE" = "--list" ]; then
    echo "=== sync-stats: live values (repo root: $REPO_ROOT) ==="
    while IFS=$'\t' read -r name cmd; do
        val="$(compute_stat "$cmd")"
        printf "  %-28s = %s\n" "$name" "$val"
    done < <(extract_stats)
    exit 0
fi

# Find docs that contain any stat block.
# Note: we deliberately do NOT prune dev-rules/ — the submodule's own README/global
# files declare stats too, and they must be scanned from the parent repo's preflight.
find_doc_files() {
    find "$REPO_ROOT" \
        \( -name .git -o -name node_modules -o -name backups -o -name old -o -name '.stats.json' \) -prune \
        -o \( -name '*.md' -o -name '*.mdc' \) -print 2>/dev/null
}

# Replace stat block contents in a single file. Echoes lines that changed.
# Args: $1 file, $2 stat_name, $3 new_value
update_file() {
    local file="$1" name="$2" newval="$3"
    # Use perl for portable in-place regex with capture
    # Pattern: <!-- stat:NAME -->ANYTHING<!-- /stat -->
    # Replace ANYTHING with $newval. Match must be on a single line (no newlines in stat blocks).
    perl -i -pe "s|(<!-- stat:\Q$name\E -->)[^<]*?(<!-- /stat -->)|\${1}\Q$newval\E\${2}|g" "$file"
}

# Check stat block contents in a single file. Returns 0 if all match, 1 if drift.
# Echoes drift lines: file:line stat=NAME doc=X live=Y
check_file() {
    local file="$1" name="$2" expected="$3"
    local drift=0
    while IFS=: read -r lineno content; do
        # Extract value between markers on this line
        actual=$(echo "$content" | sed -nE "s|.*<!-- stat:$name -->([^<]*)<!-- /stat -->.*|\1|p")
        if [ -n "$actual" ] && [ "$actual" != "$expected" ]; then
            echo "  DRIFT: $file:$lineno stat=$name doc='$actual' live='$expected'"
            drift=1
        fi
    done < <(grep -nE "<!-- stat:$name -->[^<]*<!-- /stat -->" "$file" 2>/dev/null)
    return $drift
}

total_drift=0
total_updated=0
declare -a CHANGED_FILES

while IFS=$'\t' read -r name cmd; do
    expected="$(compute_stat "$cmd")"
    if [ -z "$expected" ]; then
        echo "  WARN: stat '$name' compute returned empty (cmd: $cmd)"
        continue
    fi

    while IFS= read -r f; do
        [ -f "$f" ] || continue
        # Quick filter: file must contain this stat block
        grep -qE "<!-- stat:$name -->" "$f" 2>/dev/null || continue

        if [ "$MODE" = "--check" ]; then
            if ! check_file "$f" "$name" "$expected"; then
                total_drift=$((total_drift + 1))
            fi
        else
            # --update mode
            before=$(grep -cE "<!-- stat:$name -->[^<]*<!-- /stat -->" "$f")
            update_file "$f" "$name" "$expected"
            # Detect whether file was actually modified by perl
            if ! git diff --quiet -- "$f" 2>/dev/null; then
                rel="${f#$REPO_ROOT/}"
                CHANGED_FILES+=("$rel:$name")
                total_updated=$((total_updated + 1))
            fi
        fi
    done < <(find_doc_files)
done < <(extract_stats)

if [ "$MODE" = "--check" ]; then
    if [ "$total_drift" -eq 0 ]; then
        echo "sync-stats: ok (no drift in repo root: $REPO_ROOT)"
        exit 0
    else
        echo ""
        echo "sync-stats: $total_drift drift instance(s). Run: dev-rules/sync-stats.sh --update"
        exit 1
    fi
else
    if [ "$total_updated" -eq 0 ]; then
        echo "sync-stats: ok (all $((${#CHANGED_FILES[@]})) blocks already in sync)"
    else
        echo "sync-stats: updated $total_updated stat block(s):"
        for entry in "${CHANGED_FILES[@]}"; do
            echo "  - $entry"
        done
    fi
fi
