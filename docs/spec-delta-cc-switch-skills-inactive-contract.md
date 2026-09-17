# spec delta: dev-rules inactive contract for CC Switch Skills Core

## Background

[cc-switch `docs/approved/design-skills-core.md`](https://github.com/youxuanxue/cc-switch/blob/docs/design-skills-core/docs/approved/design-skills-core.md)（PR #3）将本机 Skill 安装、启用与 runtime 链接的长期 writer 定为 **CC Switch Skills Core**。`agent-skills` 继续只做 catalog / provenance；**dev-rules 不再担任 home 层 skill symlink writer**。

当前 dev-rules 仍通过 `sync.sh` 写入：

- `~/.cursor/skills`（additive registry）
- `~/.claude/skills` → `~/.cursor/skills`
- `~/.codex/skills/<name>`
- `~/.gemini/antigravity-cli/skills/<name>`

在 cc-switch 对某 runtime 标记 `managed` 后，dev-rules 若继续写入，将与 cc-switch 冲突。本 delta 定义 **inactive contract**：读 ownership marker，对 managed runtime fail-closed 跳过写入，并输出机器可读状态供 `cc-switch skills doctor` 校验。

**前置条件（本 PR 不得早于）：**

- cc-switch design doc merge
- cc-switch Core 至少交付：`~/.cc-switch/skills-control.json`、`cc-switch skills doctor --json`、目标 runtime 的 managed 接管能力

**审批锚点：** cc-switch `docs/approved/design-skills-core.md`（merge 后 SHA 写入 dev-rules PR body）

## Delta

### ADDED

- **`scripts/cc_switch_skills_control.sh`**（或等价模块）：解析 `~/.cc-switch/skills-control.json`
  - 字段：`schema`、`owner`（必须为 `cc-switch`）、`managed_runtimes[]`
  - 函数：`cc_switch_skills_runtime_mode <runtime_id>` → `managed` | `legacy` | `absent`
  - 函数：`cc_switch_skills_writer_status` → 单行 JSON 或 key=value，供 doctor 消费
- **inactive 守卫**：在 `sync.sh` home skill 写入前查询 marker；`managed` 时 **skip** 对应段，打印 `skills-writer: inactive (cc-switch managed: <runtime>)`
- **`sync.sh --check` 行为**：对 managed runtime **不**再检查 dev-rules-owned skill links；改为 `skip: cc-switch managed` 或 WARN（非 FAIL）
- **`verify-rules.sh`**：home cursor/codex/antigravity skill drift 段在对应 runtime managed 时 skip
- **文档口径**：`README.md`、`rules/dev-rules-convention.mdc` 声明 home 启用归 cc-switch；保留项目 `.cursor/skills` 编辑与 fan-out

### MODIFIED

- **`sync.sh` `sync_to_home`**：rules/commands/AGENTS.md/launcher 同步 **不变**；仅 skill link 段受 inactive 守卫
- **`sync.sh` `--status`**：增加 `Skills writer (home)` 小节，列出 cursor/claude-cursor、codex、antigravity 的 `active|inactive|legacy(no marker)`

### REMOVED

- **无**（本 PR 不删 legacy writer 代码；删除留给 cutover 稳定后的 follow-up PR）

### UNCHANGED

- 项目级 `link_project_skill_consumer_dir`（`.claude/.codex/.agents/skills` → 项目 `.cursor/skills`）
- `~/Codes/dev-rules/.cursor/skills` → agent-skills 镜像布局
- `check_codex_skill_limits.py`（在 agent-skills 源收敛 description）
- 规则、宪法、hooks、launcher、LaunchAgent fan-out

## Runtime 映射

dev-rules 现有写入段 ↔ cc-switch `managed_runtimes` id：

| dev-rules `sync.sh` 段 | cc-switch runtime id | managed 时 dev-rules 行为 |
| --- | --- | --- |
| `~/.cursor/skills` + `~/.claude/skills` | `claude-cursor`（耦合组，见 cc-switch 设计） | skip 两段 |
| `~/.codex/skills/*` | `codex` | skip skills 段；**保留** `~/.codex/AGENTS.md` |
| `~/.gemini/antigravity-cli/skills/*` | `antigravity` | skip skills 段；**保留** AGENTS.md 规则链 |

marker 不存在或 runtime 不在 `managed_runtimes`：**legacy**，dev-rules 行为与 today 相同。

## Scenarios

### 核心正向

1. **Given** 无 `skills-control.json` **When** `sync.sh` **Then** home skill 写入与 today 相同（legacy）
2. **Given** marker 含 `managed_runtimes: ["codex"]` **When** `sync.sh` **Then** 跳过 `~/.codex/skills` reconcile；仍同步 `~/.codex/AGENTS.md` 与 cursor/claude/antigravity 段
3. **Given** marker 含 `claude-cursor` **When** `sync.sh` **Then** 不创建/修改 `~/.cursor/skills` 与 `~/.claude/skills` 链接
4. **Given** codex managed **When** `sync.sh --check` **Then** 不对 codex skills 报 dev-rules drift FAIL

### 核心负向

5. **Given** marker 存在且 codex managed **When** dev-rules 仍尝试写入 `~/.codex/skills/foo`（守卫 bug） **Then** 测试/自检 FAIL
6. **Given** cc-switch doctor 报告 dev-rules writer 仍为 active 于 managed runtime **When** cutover 门禁 **Then** 阻塞接管（由 cc-switch 侧验收；dev-rules 提供 `writer_status` 输出）

### 回归

7. **Given** 无 marker **When** `verify-rules.sh` **Then** 现有 home skill drift 检查仍 PASS（与 today 一致）
8. **Given** 任意 marker 状态 **When** 项目 fan-out `link_skills_dir` **Then** 不受影响

## Validation

```bash
# dev-rules 子模块内（实现 PR 合并后）
cd dev-rules
./verify-rules.sh

# 无 marker — legacy 行为
rm -f ~/.cc-switch/skills-control.json
./sync.sh --check

# 模拟 managed（测试 fixture 或手工 marker）
# 见 plans/2026-08-28-cc-switch-skills-inactive-contract.md Task 3

# cc-switch 侧（cutover 时）
cc-switch skills doctor --json   # legacy writer inactive 于 managed runtime
```

实现 PR 必须包含：`scripts/*` 单测或 `verify-rules.sh` 内 self-test 段覆盖 scenario 1、2、5。

## 不在本 delta 范围

- cc-switch Core 实现
- agent-skills README writer 声明（独立 PR）
- Twin `setup.py` inactive（Twin 仓库）
- 删除 dev-rules legacy writer 代码（follow-up：`spec-delta` 或 PR title 含 `remove home skill writer`）
- preflight 硬调用 `cc-switch skills doctor`（记入 `docs/preflight-debt.md`，待 doctor 稳定后硬化）
