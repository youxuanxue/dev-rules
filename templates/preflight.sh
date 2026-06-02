#!/usr/bin/env bash
#
# preflight.sh — 消费项目提交前/CI 门禁模板。
# 各检查按前置工件自跳过；项目只有特异检查时才需要 wrapper。
# 用法：./scripts/preflight.sh 或 ./scripts/preflight.sh --fix。

set -u

# Resolve project root robustly so the script works whether invoked:
#   - directly as $project/scripts/preflight.sh
#   - via a wrapper that exec's $project/dev-rules/templates/preflight.sh
#   - from any cwd
# Strategy: prefer git toplevel of the current directory (caller's cwd),
# fall back to PREFLIGHT_REPO_ROOT env var, finally to script-relative path.
if [ -n "${PREFLIGHT_REPO_ROOT:-}" ] && [ -d "$PREFLIGHT_REPO_ROOT" ]; then
    REPO_ROOT="$PREFLIGHT_REPO_ROOT"
elif git_top="$(git rev-parse --show-superproject-working-tree 2>/dev/null)" && [ -n "$git_top" ]; then
    REPO_ROOT="$git_top"
elif git_top="$(git rev-parse --show-toplevel 2>/dev/null)" && [ -n "$git_top" ]; then
    REPO_ROOT="$git_top"
else
    REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi
cd "$REPO_ROOT"
echo "preflight: repo root = $REPO_ROOT"

FIX_MODE=0
[ "${1:-}" = "--fix" ] && FIX_MODE=1

# Resolve a usable Python interpreter (some macOS / minimal Linux installs only
# have python3, not python). Sections 4 + 5 use $PYTHON_BIN instead of bare
# `python` to avoid `command not found` failures.
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)}"

errors=0
section() { echo ""; echo "=== $* ==="; }
fail()    { echo "  FAIL: $*"; errors=$((errors + 1)); }
ok()      { echo "  ok: $*"; }
skip()    { echo "  skip: $*"; }
# Note: no "default vs conditional" group labels — every check below
# self-skips when its prerequisite is missing, so the distinction adds
# noise without changing behavior. Run order is deterministic top-to-bottom.

# Run `git ...` inside a sub-path (e.g. a submodule), isolated from any
# parent-process GIT_* env leakage. When this preflight is invoked from a
# git hook (pre-commit, commit-msg, …) git exports GIT_DIR / GIT_WORK_TREE
# / GIT_INDEX_FILE pointing at the *outer* repo. A naive `(cd dev-rules &&
# git cat-file -e $sub_sha)` then asks the OUTER git for the inner
# submodule's SHA — which is, by construction, not present, so the check
# fails with a misleading "submodule SHA not found" message inside hooks
# while passing fine when run directly. Symptom: every worktree-based
# commit fails preflight § 2 spuriously and the operator has to use
# --no-verify, eroding the entire gate.
#
# Fix: explicitly `unset` every git-context env var inside the subshell
# before invoking git; that forces git to re-derive its repo from the
# subshell's cwd. Keeping the unset list aligned with `git --help
# environment` (the variables git itself documents as "context").
git_sub() {
    local subdir="$1"; shift
    (
        cd "$subdir" || exit 2
        unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_NAMESPACE \
              GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES \
              GIT_COMMON_DIR GIT_PREFIX
        git "$@"
    )
}

# ---- 检查 1: 分支命名 ----（对应 product-dev.mdc 分支命名规范）
# merge/* 用于上游合并（CLAUDE.md §5.y `merge/upstream-YYYY-MM-DD`）
# cursor/* 用于云端 Coding Agent（如 Cursor Background Agent）的会话分支：
#   该前缀由托管平台强制（"the head branch must start with cursor/<sessionId>"），
#   开发者无法改成 fix/feature 等业务前缀；CI 必须接受它，否则所有云端 agent
#   提交的 PR 都会卡在 preflight 而无法演进。Squash-merge 时人类 reviewer 会把
#   PR title 重写成业务化文案（fix(...)/feat(...)），最终落入 main 的 commit
#   message 仍保持业务前缀。
section "branch naming (prototype/|feature/|feat/|fix/|chore/|docs/|merge/|cursor/|main|master)"
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
case "$branch" in
    main|master|prototype/*|feature/*|feat/*|fix/*|chore/*|docs/*|merge/*|cursor/*|HEAD)
        ok "branch '$branch'"
        ;;
    *)
        fail "branch '$branch' does not match required prefix"
        ;;
esac

# ---- 检查 2: dev-rules submodule-first 提交顺序 ----（对应 dev-rules-convention.mdc）
# Uses git_sub() so this works under git hooks too — see note above the helper.
section "dev-rules submodule pointer is reachable on remote"
if [ -f .gitmodules ] && grep -q "dev-rules" .gitmodules; then
    sub_sha="$(git submodule status dev-rules | awk '{print $1}' | sed 's/^[+-]//')"
    if git_sub dev-rules cat-file -e "$sub_sha" 2>/dev/null; then
        ok "submodule SHA $sub_sha exists locally in dev-rules"
        # remote check (warn-only, may fail if offline)
        if git_sub dev-rules fetch --quiet origin 2>/dev/null && \
           git_sub dev-rules merge-base --is-ancestor "$sub_sha" origin/main 2>/dev/null; then
            ok "submodule SHA is reachable on dev-rules origin/main"
        else
            echo "  warn: cannot verify submodule SHA on remote (offline or not pushed yet)"
        fi
    else
        fail "submodule SHA $sub_sha not found in dev-rules — submodule was not committed first"
    fi
else
    skip "dev-rules submodule not configured"
fi

# ---- 检查 3: dev-rules drift ----（对应 sync.sh --check）
section "dev-rules sync drift"
if [ -x dev-rules/sync.sh ]; then
    if [ "$FIX_MODE" -eq 1 ]; then
        dev-rules/sync.sh --local && ok "synced from submodule"
    else
        if dev-rules/sync.sh --check > /tmp/preflight-sync.log 2>&1; then
            ok "no drift between .cursor/rules/ and submodule"
        else
            cat /tmp/preflight-sync.log | sed 's/^/    /'
            fail ".cursor/rules/ has drifted from submodule (re-run with --fix)"
        fi
    fi
else
    skip "dev-rules/sync.sh not available"
fi

# ---- 检查 4: WebUI/API/CLI/MCP 契约不漂移 ----（对应 agent-contract-enforcement.mdc）
section "agent contract drift"
if [ -f scripts/export_agent_contract.py ]; then
    if "$PYTHON_BIN" scripts/export_agent_contract.py --check > /tmp/preflight-contract.log 2>&1; then
        ok "contract docs in sync with code"
    else
        cat /tmp/preflight-contract.log | sed 's/^/    /'
        fail "contract docs have drifted (regenerate via '$PYTHON_BIN scripts/export_agent_contract.py')"
    fi
else
    skip "scripts/export_agent_contract.py not present (enable for contract-bearing projects)"
fi

# ---- 检查 5: User Story ↔ Test 漂移 ----（对应 test-philosophy.mdc）
section "user story / test alignment"
if [ -f .testing/user-stories/verify_quality.py ]; then
    if "$PYTHON_BIN" .testing/user-stories/verify_quality.py > /tmp/preflight-stories.log 2>&1; then
        ok "stories aligned with tests"
    else
        cat /tmp/preflight-stories.log | sed 's/^/    /'
        fail "story quality / alignment check failed"
    fi
else
    skip ".testing/user-stories/verify_quality.py not present (story workflow not enabled)"
fi

# ---- 检查 6: docs/approved/ 不在非 GATE PR 中被修改 ----（对应 product-dev.mdc 阶段 2）
section "docs/approved/ change discipline"
if [ -d docs/approved ]; then
    base="${PREFLIGHT_BASE:-origin/main}"
    if git rev-parse --verify "$base" >/dev/null 2>&1; then
        approved_changed="$(git diff --name-only "$base"...HEAD -- docs/approved/ 2>/dev/null || true)"
        if [ -n "$approved_changed" ]; then
            case "$branch" in
                prototype/*)
                    ok "docs/approved/ modified on prototype branch (allowed)"
                    ;;
                *)
                    echo "  warn: docs/approved/ modified outside prototype/* branch:"
                    echo "$approved_changed" | sed 's/^/    - /'
                    echo "  warn: PR reviewer should confirm this is an intentional approval revision"
                    ;;
            esac
        else
            ok "docs/approved/ unchanged in this branch"
        fi
    else
        skip "no '$base' to diff against"
    fi
else
    skip "docs/approved/ directory not present (high-risk design gate not enabled)"
fi

# ---- 检查 7: docs/approved/ 不变量（R1-R4 任何分支 + R5 仅 main/master） ----
# R1 frontmatter exists / R2 status valid / R3 pending+commits smell /
# R4 shipped without commits — enforced by dev-rules/scripts/check_approved_docs.py
# (universal across all consumer projects).
# R5 approved_by: pending — branch-specific, kept inline because it only blocks
# on main/master (other branches may legitimately carry pending approvers).
section "approved-doc invariants (R1-R4 universal + R5 main/master only)"
if [ -d docs/approved ]; then
    if [ -f dev-rules/scripts/check_approved_docs.py ]; then
        if "$PYTHON_BIN" dev-rules/scripts/check_approved_docs.py 2> /tmp/preflight-approved.log; then
            ok "R1-R4: all approved-doc frontmatter invariants hold"
        else
            cat /tmp/preflight-approved.log | sed 's/^/    /'
            fail "R1-R4: approved-doc invariants violated (see above)"
        fi
    else
        skip "dev-rules/scripts/check_approved_docs.py not present"
    fi

    if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
        pending=$(grep -lE '^approved_by:[[:space:]]*pending[[:space:]]*$' docs/approved/*.md 2>/dev/null || true)
        if [ -n "$pending" ]; then
            echo "$pending" | sed 's/^/    - /'
            fail "R5: files with approved_by: pending must not land on $branch"
        else
            ok "R5: all approved/* files on $branch have a real approver"
        fi
    else
        skip "R5 (approved_by: pending) only enforced on main/master, current=$branch"
    fi
else
    skip "docs/approved/ directory not present (high-risk design gate not enabled)"
fi

# ---- 检查 8: 散文档中的 stat 块与 live 计算值一致 ----（治"变更必伴漂移"）
section "doc stats vs live values (sync-stats.sh --check)"
if [ -x dev-rules/sync-stats.sh ]; then
    if [ "$FIX_MODE" -eq 1 ]; then
        dev-rules/sync-stats.sh --update | sed 's/^/    /'
        ok "stat blocks updated to live values"
    else
        if dev-rules/sync-stats.sh --check > /tmp/preflight-stats.log 2>&1; then
            ok "all stat blocks match live values"
        else
            cat /tmp/preflight-stats.log | sed 's/^/    /'
            fail "doc stats have drifted (re-run with --fix or 'dev-rules/sync-stats.sh --update')"
        fi
    fi
else
    skip "dev-rules/sync-stats.sh not available"
fi

# ---- 检查 9: cloud agent / 本地 agent 运行环境一致性 ----（对应 cloud-agent-bootstrap.sh）
# Only triggers when the project opts in by creating .cursor/cloud-agent.env.
# Reports tools missing from PATH and secrets the project declared as
# REQUIRED but are not exported in the current shell. The same script runs
# in cloud-agent install (bootstrap), in this preflight gate, and standalone
# via `bash dev-rules/templates/cloud-agent-bootstrap.sh --check`, so cloud
# agent and local agent are checked against the identical contract.
#
# Skipped on generic CI runners (GitHub Actions, GitLab CI, etc.) — those
# environments are neither cloud-agent VMs nor local dev shells, so they
# legitimately lack the agent's runtime tools and secrets. The check still
# runs in install / cloud-agent / local preflight paths where the contract
# does need to hold. Detection covers the common CI providers; project
# wrappers can opt in by exporting CLOUD_AGENT_FORCE_CHECK=1.
section "cloud-agent env consistency (tools + secrets, both local and cloud)"
if [ -f .cursor/cloud-agent.env ] && [ -x dev-rules/templates/cloud-agent-bootstrap.sh ]; then
    if [ -z "${CLOUD_AGENT_FORCE_CHECK:-}" ] && \
       { [ "${CI:-}" = "true" ] || [ -n "${GITHUB_ACTIONS:-}" ] || [ -n "${GITLAB_CI:-}" ] || [ -n "${BUILDKITE:-}" ] || [ -n "${CIRCLECI:-}" ]; }; then
        skip "generic CI runner detected (CI / GITHUB_ACTIONS / GITLAB_CI / …) — cloud-agent contract is for cloud-agent + local dev sessions; set CLOUD_AGENT_FORCE_CHECK=1 to override"
    elif CLOUD_AGENT_REPO_ROOT="$REPO_ROOT" \
         dev-rules/templates/cloud-agent-bootstrap.sh --check > /tmp/preflight-cloud-agent.log 2>&1; then
        ok "cloud-agent env consistent (tools + required secrets present)"
    else
        cat /tmp/preflight-cloud-agent.log | sed 's/^/    /'
        fail "cloud-agent env inconsistent (missing required tool or secret — see above)"
    fi
else
    skip ".cursor/cloud-agent.env not present (cloud-agent contract not declared for this project)"
fi

# ---- 检查 10: 公共契约删除必须有显式说明锚点 ----
section "contract deletion notice"
if [ -f dev-rules/scripts/check_contract_deletion_notice.py ]; then
    if "$PYTHON_BIN" dev-rules/scripts/check_contract_deletion_notice.py --base "${PREFLIGHT_BASE:-origin/main}" > /tmp/preflight-contract-delete.log 2>&1; then
        ok "contract deletion notice check passed"
    else
        cat /tmp/preflight-contract-delete.log | sed 's/^/    /'
        fail "contract deletion detected without explicit notice token"
    fi
else
    skip "dev-rules/scripts/check_contract_deletion_notice.py not present"
fi

# ---- 检查 11: 后端/业务逻辑改动必须对齐 Web surface ----
section "web surface alignment"
if [ -f dev-rules/scripts/check_web_surface_alignment.py ]; then
    if "$PYTHON_BIN" dev-rules/scripts/check_web_surface_alignment.py --base "${PREFLIGHT_BASE:-origin/main}" > /tmp/preflight-web-surface.log 2>&1; then
        ok "web surface alignment check passed"
    else
        cat /tmp/preflight-web-surface.log | sed 's/^/    /'
        fail "backend/business-logic changes missing Web/config/contract alignment evidence"
    fi
else
    skip "dev-rules/scripts/check_web_surface_alignment.py not present"
fi

# ---- 检查 12: 分层依赖不可反转（配置驱动） ----
section "layer dependency inversion"
if [ -f .preflight/layer-deps.json ]; then
    if [ -f dev-rules/scripts/check_layer_dependency_inversion.py ]; then
        if "$PYTHON_BIN" dev-rules/scripts/check_layer_dependency_inversion.py --config .preflight/layer-deps.json > /tmp/preflight-layer-deps.log 2>&1; then
            ok "layer dependency inversion check passed"
        else
            cat /tmp/preflight-layer-deps.log | sed 's/^/    /'
            fail "layer dependency inversion detected"
        fi
    else
        skip "dev-rules/scripts/check_layer_dependency_inversion.py not present"
    fi
else
    skip ".preflight/layer-deps.json not present"
fi

# ---- 检查 13: 高风险改动必须绑定审批锚点 ----
# 默认只覆盖通用高风险目录（migrations/schema）；项目可通过
# .preflight/high-risk-anchor.conf 覆写 [high_risk_paths]/[anchor_paths]/[anchor_tokens]。
section "high-risk approval anchor"
if [ -f dev-rules/scripts/check_high_risk_anchor.py ]; then
    if "$PYTHON_BIN" dev-rules/scripts/check_high_risk_anchor.py --base "${PREFLIGHT_BASE:-origin/main}" > /tmp/preflight-high-risk-anchor.log 2>&1; then
        ok "high-risk anchor check passed"
    else
        cat /tmp/preflight-high-risk-anchor.log | sed 's/^/    /'
        fail "high-risk changes missing approval anchor"
    fi
else
    skip "dev-rules/scripts/check_high_risk_anchor.py not present"
fi

# ---- 检查 14: release 语境禁止 skip-ci marker ----
# 分支/标签模式与 marker 可通过 .preflight/release-skip-ci.conf 覆写。
section "release skip-ci safety"
if [ -f dev-rules/scripts/check_release_skip_ci_safety.py ]; then
    if "$PYTHON_BIN" dev-rules/scripts/check_release_skip_ci_safety.py --base "${PREFLIGHT_BASE:-origin/main}" > /tmp/preflight-release-skip-ci.log 2>&1; then
        ok "release skip-ci safety check passed"
    else
        cat /tmp/preflight-release-skip-ci.log | sed 's/^/    /'
        fail "release-sensitive context contains forbidden skip-ci marker"
    fi
else
    skip "dev-rules/scripts/check_release_skip_ci_safety.py not present"
fi

# ---- 检查 15: GitHub Actions workflow 硬失败 pattern ----
# job-level `if: env.*`、claude -p 缺 --allowedTools、claude -p --output（不存在）。
# 三类都是 YAML lint 通不过但执行时静默失败的形态。
section "workflow yaml hard-failure patterns"
if [ -f dev-rules/scripts/check_workflow_yaml.py ]; then
    if "$PYTHON_BIN" dev-rules/scripts/check_workflow_yaml.py > /tmp/preflight-workflow-yaml.log 2>&1; then
        head -1 /tmp/preflight-workflow-yaml.log | sed 's/^/    /'
    else
        cat /tmp/preflight-workflow-yaml.log | sed 's/^/    /'
        fail "workflow YAML has hard failure patterns (see above)"
    fi
else
    skip "dev-rules/scripts/check_workflow_yaml.py not present"
fi

# ---- 检查 16: review.schema.json 校验（.reviews/*.json 存在时） ----
section "review record schema"
if [ -f dev-rules/scripts/check_review_record.py ]; then
    if "$PYTHON_BIN" dev-rules/scripts/check_review_record.py > /tmp/preflight-review-record.log 2>&1; then
        head -1 /tmp/preflight-review-record.log | sed 's/^/    /'
    else
        cat /tmp/preflight-review-record.log | sed 's/^/    /'
        fail "review record(s) violate schemas/review.schema.json"
    fi
else
    skip "dev-rules/scripts/check_review_record.py not present"
fi

# ---- 检查 17: skill.schema.json 校验（.cursor/skills/**/skill.json 存在时） ----
section "skill manifest schema"
if [ -f dev-rules/scripts/check_skill_manifest.py ]; then
    if "$PYTHON_BIN" dev-rules/scripts/check_skill_manifest.py > /tmp/preflight-skill-manifest.log 2>&1; then
        head -1 /tmp/preflight-skill-manifest.log | sed 's/^/    /'
    else
        cat /tmp/preflight-skill-manifest.log | sed 's/^/    /'
        fail "skill manifest(s) violate schemas/skill.schema.json"
    fi
else
    skip "dev-rules/scripts/check_skill_manifest.py not present"
fi

# ---- 检查 17b: Codex AGENTS.md 受管块不漂移 ----（对应 gen_codex_agents.py）
# Codex 经 <repo>/AGENTS.md 的 dev-rules 受管块消费宪法/规则/技能索引；该块由
# dev-rules/sync.sh 确定性生成，禁止手工编辑。仅当项目已落地该块时才校验。
section "codex AGENTS.md managed block"
if [ -f dev-rules/scripts/gen_codex_agents.py ] && [ -f AGENTS.md ] && \
   grep -q 'dev-rules:codex BEGIN' AGENTS.md 2>/dev/null; then
    if "$PYTHON_BIN" dev-rules/scripts/gen_codex_agents.py --project "$REPO_ROOT" --check > /tmp/preflight-codex-agents.log 2>&1; then
        ok "AGENTS.md dev-rules block in sync"
    else
        cat /tmp/preflight-codex-agents.log | sed 's/^/    /'
        fail "AGENTS.md dev-rules block drifted (run dev-rules/sync.sh --project \"$REPO_ROOT\")"
    fi
else
    skip "no Codex AGENTS.md managed block (run dev-rules/sync.sh --project to create)"
fi

# ---- 检查 17c: 技能描述不超 Codex 加载上限 ----（对应 check_codex_skill_limits.py）
# Codex 0.122 拒绝 description > 1024 字符的 SKILL.md（静默丢弃该技能）；Cursor /
# Claude Code 无此限，故为 Codex 复用而硬化。两种技能仓库布局都覆盖：消费项目放
# .cursor/skills/，技能源仓库（agent-skills）把技能放在仓库根 <name>/SKILL.md。
section "codex skill description length"
if [ -f dev-rules/scripts/check_codex_skill_limits.py ] && \
   { [ -d .cursor/skills ] || ls */SKILL.md >/dev/null 2>&1; }; then
    if "$PYTHON_BIN" dev-rules/scripts/check_codex_skill_limits.py --root "$REPO_ROOT" > /tmp/preflight-codex-skill.log 2>&1; then
        head -1 /tmp/preflight-codex-skill.log | sed 's/^/    /'
    else
        cat /tmp/preflight-codex-skill.log | sed 's/^/    /'
        fail "skill description(s) exceed Codex 1024-char limit — Codex will silently drop them"
    fi
else
    skip "no skills found (.cursor/skills or <name>/SKILL.md) to check against Codex limit"
fi

# ---- 检查 18a: 删除文件不得留下打包元数据 / frontmatter 悬空引用 ----
# preflight 不构建 wheel，hatchling 等打包后端会在 CI 才报错；硬化这条软约束。
section "deleted files not still referenced (config/frontmatter)"
if [ -f dev-rules/scripts/check_deleted_file_refs.py ]; then
    if "$PYTHON_BIN" dev-rules/scripts/check_deleted_file_refs.py > /tmp/preflight-deleted-refs.log 2>&1; then
        head -1 /tmp/preflight-deleted-refs.log | sed 's/^/    /'
    else
        cat /tmp/preflight-deleted-refs.log | sed 's/^/    /'
        fail "deleted file(s) still referenced in build config or doc frontmatter — fix reference or restore file"
    fi
else
    skip "dev-rules/scripts/check_deleted_file_refs.py not present"
fi

# ---- 检查 18: 存在性测试 AST 扫描 ----
# 仅断言文件存在/非空/行数的测试不是行为验证；test-philosophy.mdc 强约束。
section "no existence-only tests"
if [ -f dev-rules/scripts/check_existence_only_tests.py ]; then
    if "$PYTHON_BIN" dev-rules/scripts/check_existence_only_tests.py > /tmp/preflight-existence-tests.log 2>&1; then
        head -1 /tmp/preflight-existence-tests.log | sed 's/^/    /'
    else
        cat /tmp/preflight-existence-tests.log | sed 's/^/    /'
        fail "test(s) only assert file existence — replace with behavior assertions"
    fi
else
    skip "dev-rules/scripts/check_existence_only_tests.py not present"
fi

# ---- 检查 19: 本地 linter 与 CI 同源 ----
# 自动探测项目根的 linter 工具；工具不存在或缺少配置就 skip。
# 项目可通过 .preflight/local-lint.conf 覆写命令（每行一条 shell 命令，#注释）。
section "local linters in sync with CI"
LINT_CONF=".preflight/local-lint.conf"
LINT_CMDS=()
if [ -f "$LINT_CONF" ]; then
    while IFS= read -r line; do
        case "$line" in
            ''|\#*) ;;
            *) LINT_CMDS+=("$line") ;;
        esac
    done < "$LINT_CONF"
else
    has_ruff_config=0
    if [ -f ruff.toml ] || [ -f .ruff.toml ]; then
        has_ruff_config=1
    fi
    if [ -f pyproject.toml ] && grep -q '^\[tool\.ruff' pyproject.toml 2>/dev/null; then
        has_ruff_config=1
    fi
    [ -d .github/workflows ] && grep -R "ruff check" .github/workflows >/dev/null 2>&1 && has_ruff_config=1
    if [ "$has_ruff_config" -eq 1 ]; then
        if command -v ruff >/dev/null 2>&1; then
            LINT_CMDS+=("ruff check .")
        elif "$PYTHON_BIN" -c 'import ruff' >/dev/null 2>&1; then
            LINT_CMDS+=("$PYTHON_BIN -m ruff check .")
        fi
    fi

    has_eslint_config=0
    [ -d .github/workflows ] && grep -R "eslint" .github/workflows >/dev/null 2>&1 && has_eslint_config=1
    [ -f package.json ] && grep -q '"lint".*eslint' package.json 2>/dev/null && has_eslint_config=1
    if [ "$has_eslint_config" -eq 1 ] && command -v npx >/dev/null 2>&1; then
        LINT_CMDS+=("npx --no-install eslint .")
    fi
fi
if [ ${#LINT_CMDS[@]} -eq 0 ]; then
    skip "no local linter detected (set .preflight/local-lint.conf to enable)"
else
    lint_errors=0
    for cmd in "${LINT_CMDS[@]}"; do
        if eval "$cmd" > /tmp/preflight-local-lint.log 2>&1; then
            ok "$cmd"
        else
            cat /tmp/preflight-local-lint.log | sed 's/^/    /'
            fail "linter failed: $cmd"
            lint_errors=$((lint_errors + 1))
        fi
    done
fi

# ---- 检查 20: 静默吞错形态（|| true / --no-verify / except: pass / continue-on-error） ----
# warn-only：合法 cleanup (rm ... || true) 普遍存在，硬失败会过吵；这里只列出
# diff 新增的吞错点供 review，模型判断是否掩盖真实失败。行内 `# preflight-allow: swallow`
# 可确定性豁免。项目可设 SILENT_SWALLOW_STRICT=1 升级为硬门禁。
section "silent-error-swallow sites (warn-only)"
if [ -f dev-rules/scripts/check_silent_error_swallow.py ]; then
    strict_flag=""
    [ -n "${SILENT_SWALLOW_STRICT:-}" ] && strict_flag="--strict"
    if "$PYTHON_BIN" dev-rules/scripts/check_silent_error_swallow.py --base "${PREFLIGHT_BASE:-origin/main}" $strict_flag > /tmp/preflight-silent-swallow.log 2>&1; then
        head -1 /tmp/preflight-silent-swallow.log | sed 's/^/    /'
        [ -s /tmp/preflight-silent-swallow.log ] && tail -n +2 /tmp/preflight-silent-swallow.log | sed 's/^/    /'
    else
        cat /tmp/preflight-silent-swallow.log | sed 's/^/    /'
        fail "silent-error-swallow added in diff (SILENT_SWALLOW_STRICT mode)"
    fi
else
    skip "dev-rules/scripts/check_silent_error_swallow.py not present"
fi

echo ""
if [ $errors -eq 0 ]; then
    echo "=== preflight: PASS ==="
    exit 0
else
    echo "=== preflight: FAIL ($errors check(s) failed) ==="
    exit 1
fi
