#!/usr/bin/env bash
#
# dev-rules/verify-rules.sh — 验证规则仓库自身完整性
#
# 检查内容：
#   1. 每个 .mdc 含 YAML frontmatter（description / alwaysApply）
#   2. README 表格中列出的所有规则/命令文件真实存在
#   3. 反向：rules/ 与 commands/ 中的所有文件均在 README 表中出现
#   4. commands/*.md 不含未定义的 placeholder（{{...}}）
#   5. 哲学映射表（README §设计哲学）覆盖所有 rules + commands
#   6. 仓库内引用的 dev-rules/ 路径（rules/commands/global/README）真实存在（防"幽灵引用"）
#   7. global/ 目录关键文件存在（CLAUDE.md）
#
# 用法：
#   ./verify-rules.sh           # 验证，发现问题非零退出
#   ./verify-rules.sh --quiet   # 仅在失败时输出
#
# 退出码：
#   0 = 全部通过
#   1 = 至少一项失败

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RULES_DIR="$SCRIPT_DIR/rules"
COMMANDS_DIR="$SCRIPT_DIR/commands"
GLOBAL_DIR="$SCRIPT_DIR/global"
README="$SCRIPT_DIR/README.md"

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

errors=0
log() { [ "$QUIET" -eq 0 ] && echo "$@"; }
fail() { echo "  FAIL: $*"; errors=$((errors + 1)); }
ok()   { [ "$QUIET" -eq 0 ] && echo "  ok: $*"; }

log "=== verify-rules: dev-rules repo integrity ==="
log ""

# Check 1: frontmatter
log "[1/7] frontmatter on every rule"
for rule in "$RULES_DIR"/*.mdc; do
    base="$(basename "$rule")"
    head -n 1 "$rule" | grep -q '^---$' || { fail "$base: missing leading ---"; continue; }
    grep -q '^description:' "$rule" || fail "$base: missing description field"
    grep -q '^alwaysApply:' "$rule" || fail "$base: missing alwaysApply field"
    [ "$(grep -c '^---$' "$rule")" -ge 2 ] || fail "$base: missing closing ---"
    ok "$base"
done

# Check 2: README rules table → real files
log ""
log "[2/7] README rule references resolve"
if [ ! -f "$README" ]; then
    fail "README.md not found at $README"
else
    referenced_rules=$(grep -oE '`rules/[a-z-]+\.mdc`' "$README" | sort -u | sed 's/`//g')
    for ref in $referenced_rules; do
        if [ -f "$SCRIPT_DIR/$ref" ]; then
            ok "$ref"
        else
            fail "$ref referenced in README but file missing"
        fi
    done

    referenced_cmds=$(grep -oE '`commands/[a-z_-]+\.md`' "$README" | sort -u | sed 's/`//g')
    for ref in $referenced_cmds; do
        if [ -f "$SCRIPT_DIR/$ref" ]; then
            ok "$ref"
        else
            fail "$ref referenced in README but file missing"
        fi
    done
fi

# Check 3: every rule/command appears in README
# Accept either path-prefixed (`rules/foo.mdc`) or bare backtick (`foo.mdc`).
log ""
log "[3/7] every rule/command is documented in README"
for rule in "$RULES_DIR"/*.mdc; do
    base="$(basename "$rule")"
    if grep -qE "(rules/$base|\`$base\`)" "$README" 2>/dev/null; then
        ok "$base"
    else
        fail "$base exists but not mentioned in README"
    fi
done
for cmd in "$COMMANDS_DIR"/*.md; do
    base="$(basename "$cmd")"
    cmd_name="${base%.md}"
    if grep -qE "(commands/$base|\`$base\`|/user:$cmd_name|\`$cmd_name\`)" "$README" 2>/dev/null; then
        ok "$base"
    else
        fail "$base exists but not referenced in README"
    fi
done

# Check 4: commands have no unresolved placeholders
log ""
log "[4/7] commands free of unresolved {{placeholders}}"
for cmd in "$COMMANDS_DIR"/*.md; do
    base="$(basename "$cmd")"
    if grep -qE '\{\{[^}]+\}\}' "$cmd"; then
        # whitelist arguments-like tokens that are intentional
        offenders=$(grep -nE '\{\{[^}]+\}\}' "$cmd" | grep -vE '\{\{ARGUMENTS\}\}|\{\{date.*\}\}' || true)
        if [ -n "$offenders" ]; then
            fail "$base contains unresolved placeholders:"
            echo "$offenders" | sed 's/^/    /'
        else
            ok "$base"
        fi
    else
        ok "$base"
    fi
done

# Check 5: philosophy mapping coverage
log ""
log "[5/7] philosophy mapping covers all rules+commands"
if grep -q '## 设计哲学' "$README" 2>/dev/null; then
    # Extract the 设计哲学 section content (until next top-level ## heading)
    philosophy_section=$(awk '/^## 设计哲学/{flag=1; next} /^## /{flag=0} flag' "$README")
    for rule in "$RULES_DIR"/*.mdc; do
        base="$(basename "$rule")"
        if echo "$philosophy_section" | grep -qE "(rules/$base|\`$base\`)"; then
            ok "$base in philosophy table"
        else
            fail "$base missing from philosophy mapping table"
        fi
    done
    for cmd in "$COMMANDS_DIR"/*.md; do
        base="$(basename "$cmd")"
        cmd_name="${base%.md}"
        if echo "$philosophy_section" | grep -qE "(commands/$base|\`$base\`|/user:$cmd_name|\`$cmd_name\`)"; then
            ok "$base in philosophy table"
        else
            fail "$base missing from philosophy mapping table"
        fi
    done
else
    fail "README missing '## 设计哲学' section"
fi

# Check 6: every dev-rules/ path mentioned in rules/commands/global/README must exist
log ""
log "[6/7] dev-rules/ path references resolve (no ghost paths)"
ghost=0
# Collect all `dev-rules/...` references inside backticks across this repo
while IFS= read -r path; do
    # Strip CLI flags like '--check' / '--local' if accidentally captured
    clean="$(echo "$path" | awk '{print $1}')"
    # Resolve relative to dev-rules root (SCRIPT_DIR is dev-rules/)
    rel="${clean#dev-rules/}"
    target="$SCRIPT_DIR/$rel"
    if [ -e "$target" ]; then
        ok "$clean"
    else
        fail "$clean referenced but not present at $target"
        ghost=1
    fi
done < <(grep -rhoE '`dev-rules/[A-Za-z0-9_./-]+`' "$RULES_DIR" "$COMMANDS_DIR" "$GLOBAL_DIR" "$README" 2>/dev/null \
         | sed -E 's/^`(.*)`$/\1/' | sort -u)
[ "$ghost" -eq 0 ] || true

# Check 7: global/ key files exist
log ""
log "[7/7] global/ key files present"
if [ -f "$GLOBAL_DIR/CLAUDE.md" ]; then
    ok "global/CLAUDE.md"
else
    fail "global/CLAUDE.md missing — sync.sh symlinks ~/.claude/CLAUDE.md to it"
fi

log ""
if [ $errors -eq 0 ]; then
    log "=== PASS: 0 errors ==="
    exit 0
else
    echo "=== FAIL: $errors error(s) ==="
    exit 1
fi
