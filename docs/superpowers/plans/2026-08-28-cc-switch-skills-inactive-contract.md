# dev-rules × CC Switch Skills Core — inactive contract 实施计划

> **For agentic workers:** 使用 `$git-worktree-submodule` 建隔离 worktree；实现前确认 cc-switch design + Core 门禁已满足 spec delta 前置条件。

**Goal:** dev-rules 在 cc-switch 已 managed 的 runtime 上停止 home skill 写入，并输出机器可读 writer 状态；不破坏 legacy 机器与项目级 skill fan-out。

**Architecture:** 新增只读 marker 解析模块；`sync.sh` / `verify-rules.sh` 在 skill 段入口查询 runtime mode；文档改口径。本计划 **不删除** legacy writer 实现（留给 cutover 稳定后的清理 PR）。

**Spec delta:** `docs/spec-delta-cc-switch-skills-inactive-contract.md`

**审批锚点:** cc-switch [`docs/approved/design-skills-core.md`](https://github.com/youxuanxue/cc-switch/blob/docs/design-skills-core/docs/approved/design-skills-core.md)

## Global Constraints

- PR 不得早于 cc-switch Core 交付 `skills-control.json` 解析约定与 `doctor --json` 中的 legacy writer 检查。
- dev-rules 子模块先 commit + push，再更新父仓库 pointer（标准 submodule 链路）。
- 禁止在 inactive 守卫失败时 silent fallback 到写入——managed 必须 skip，不得「试着写一下」。
- `~/.codex/AGENTS.md`、`~/.gemini/antigravity-cli/AGENTS.md` 等 **规则链** 仍由 dev-rules 维护；仅 **skills 链接** 受 inactive 约束。
- foreign entry 规则不变：dev-rules 从未接管 foreign，inactive 后也不得碰 cc-switch-owned links。

## File Structure

**新增:**

```text
scripts/cc_switch_skills_control.sh    # marker 解析 + runtime mode
scripts/test_cc_switch_skills_control.sh  # 或 verify-rules.sh 内 self-test
```

**修改:**

```text
sync.sh
verify-rules.sh
rules/dev-rules-convention.mdc
README.md
docs/preflight-debt.md                 # 可选：doctor 硬化 debt 条目
```

**不修改（本计划）:**

```text
templates/preflight.sh                 # 暂不硬依赖 cc-switch doctor
global/CLAUDE.md                       # 除非已有 home skill 单句需改口径
项目 fan-out link_skills_dir 路径
```

---

## Task 0: 门禁确认

- [ ] cc-switch design PR merge；记录 `related_commits` / SHA
- [ ] cc-switch Core PR 已提供：
  - `~/.cc-switch/skills-control.json` schema v1
  - `managed_runtimes` 含 `claude-cursor`、`codex`、`antigravity` 语义
  - `cc-switch skills doctor --json` 含 `legacy_writers.dev_rules`（或等价字段）
- [ ] agent-skills README writer 声明 PR 已合并或与本 PR 并行无冲突

---

## Task 1: marker 解析模块

**Files:**

- Create: `scripts/cc_switch_skills_control.sh`
- Create: `scripts/test_cc_switch_skills_control.sh`（或 extend `verify-rules.sh`）

**接口:**

```bash
# 返回 0；stdout: managed|legacy|absent
cc_switch_skills_runtime_mode "<runtime_id>"

# 返回 0；stdout 一行 JSON
cc_switch_skills_writer_status
```

**行为:**

| 条件 | mode |
| --- | --- |
| marker 不存在 | `absent` → 所有 runtime **legacy** |
| marker `owner` ≠ `cc-switch` |  WARN + 所有 runtime **legacy**（fail closed 不写入 unknown owner） |
| runtime ∈ `managed_runtimes` | **managed** |
| 否则 | **legacy** |

- [ ] 实现 JSON 解析（依赖 `jq` 或 Python3；与 dev-rules 现有脚本风格一致）
- [ ] 测试：无 marker、空 managed、单 runtime、非法 owner、非法 JSON
- [ ] 接入 `verify-rules.sh` self-test 段

---

## Task 2: sync.sh inactive 守卫

**Files:**

- Modify: `sync.sh`

**改动点:**

1. `source scripts/cc_switch_skills_control.sh`（或内联等价，优先可测试函数）
2. **`sync_to_home` 内 cursor/claude 段** — 若 `claude-cursor` 为 managed → skip + log inactive
3. **`sync_to_codex_home` skills 段** — 若 `codex` managed → skip；AGENTS.md 段保留
4. **`sync_to_antigravity_home` skills 段** — 若 `antigravity` managed → skip
5. **`--status`** — 打印各 runtime writer mode

**Check 段:**

- `check_home_cursor_skills_drift` / `check_home_skills_drift` — claude-cursor managed → skip
- `check_home_codex_drift` skills 部分 — codex managed → skip
- antigravity 同理

- [ ] 手工：无 marker，`sync.sh --check` 与 today 同结果
- [ ] 手工：fixture marker codex managed，codex skills drift 不 FAIL
- [ ] 确认 `sync.sh --local` / fan-out **不**因 skip 而 exit 1

---

## Task 3: verify-rules + 文档

**Files:**

- Modify: `verify-rules.sh`
- Modify: `rules/dev-rules-convention.mdc`
- Modify: `README.md` Agent Skills 小节
- Modify: `docs/preflight-debt.md`（可选）

**文档要点:**

- Home 启用：**cc-switch**（`skills-control.json` + SQLite activation）
- dev-rules：**编辑**在 agent-skills / 项目 `.cursor/skills`；**分发规则**不变
- 引用 cc-switch approved doc URL（merge 后换 main 链）

- [ ] `./verify-rules.sh` 全绿
- [ ] `sync.sh --check` 全绿（legacy 与 managed fixture 各跑一次）

---

## Task 4: PR 与 fan-out

- [ ] dev-rules 子模块 commit + `./sync.sh --push`
- [ ] 父仓库（如有）更新 submodule pointer + `.cursor/rules/`
- [ ] PR body 中文：`摘要` / `风险` / `验证` / `提交`；绑定 cc-switch approved SHA
- [ ] commit subject 含 `no-web-impact`（若仅 dev-rules 子模块元仓库）

---

## Task 5（follow-up，本计划外）: 删除 legacy writer

**触发条件:** 本机所有目标 runtime 已 managed 且 `cc-switch skills doctor --json` 连续稳定。

**删除范围:**

- `sync.sh` 内 `reconcile_owned_skill_links` home 调用路径
- `check_home_*_skills_drift` 段
- `cc_switch_skills_control.sh` 的 legacy 分支（或保留 absent=全 skip 无写入）

单独 PR；标题明确 `remove home skill writer`。

---

## Cutover 操作手册（人工，非 dev-rules 代码）

对每个 runtime（建议顺序：codex → antigravity → claude-cursor）：

```bash
# 1. cc-switch 预检
cc-switch skills doctor --json

# 2. 显式接管
cc-switch skills sync --runtime <id> --mode managed

# 3. 确认 dev-rules inactive
dev-rules/sync.sh --status    # 对应 runtime 应显示 inactive
dev-rules/sync.sh --check     # 不应因 dev-rules-owned skills 失败

# 4. 重启相关 Agent（按 doctor reload hint）
```

`claude-cursor` 接管前：确认 doctor 已处理 `~/.claude/skills` 非 symlink 情况；必要时先 `import` legacy active set。

---

## 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| marker 误标 managed，dev-rules 不写导致 skill 真空 | doctor 阻塞接管；回滚：`cc-switch skills sync --runtime <id> --mode legacy` + 移除 runtime from marker |
| dev-rules 仍写 managed 路径 | Task 1 测试 + cc-switch doctor legacy_writers 检查 |
| 文档与行为漂移 | spec delta scenario 7–8 + verify self-test |

---

## 完成定义

- [ ] managed runtime 上 `sync.sh` 零 skill 链接写入
- [ ] legacy（无 marker）行为与当前 main 一致
- [ ] `cc_switch_skills_writer_status` 可被 cc-switch doctor 消费
- [ ] 项目 fan-out 与 rules 同步无回归
