运行 xuejiao 分身监督 code agent。

$ARGUMENTS

## 命令行为

你是 `/twin` 的执行入口，不是说明书。根据 `$ARGUMENTS` 直接选择一个最小动作：

- 没有参数或只有项目路径：先读 `<project>/.xuejiao-twin/CURRENT.md`；如果不存在，提示先 `init`。
- `init ...`：运行初始化命令，返回 workspace 和下一条 `run` 命令。
- `run ...`：执行一次 supervised run，随后读 `CURRENT.md` 并只汇报 outcome、focus、next。
- `next ...`：仅作为 `run --mode supervised-normal` 的别名；必须转换为 `PYTHONPATH=/Users/xuejiao/Codes/dev-rules python3 -m scripts.xuejiao_twin run ... --mode supervised-normal`，禁止把 `next` 直接传给底层 CLI。

**所有调用 `python3 -m scripts.xuejiao_twin` 的命令都必须加前缀 `PYTHONPATH=/Users/xuejiao/Codes/dev-rules`**（除非 cwd 已经在 dev-rules 仓库根目录）。模块物理上在 dev-rules，从 zw-brain 等其它项目运行时不带 PYTHONPATH 会 `No module named scripts.xuejiao_twin`。
- `status ...` / `current ...`：只读 `CURRENT.md`，不调用 agent。
- `respond ...`：写 human response 后给下一条 `run` 命令。
- `replan ...`：重置 dynamic ledger，随后读 `CURRENT.md`。
- `validate ...`：运行 fixture 或 run artifact 验证。
- `doctor ...`（roadmap）：只读体检 workspace + 工具链（claude、git、hook gate、ledger schema、settings 来源）。
- `loop ...`：给出或执行 `/loop` 续跑用法，循环体只做一次 `run`，不要在同一轮里手写 while 循环。

默认保持乔布斯式输出：一个状态、一条下一步、必要证据路径；不要重复 runbook 大段内容。

### `needs_human` 展示契约

当 `run` / `next` 的 outcome 为 `needs_human`（包括 blocked latch），**必须 inline 输出以下三项**，禁止折叠到"去看 CURRENT.md"：

1. **决策背景**：`CURRENT.md` 中的 `trigger` + `summary`（以及 `blocked_features` 的 id、描述、blocked_reason，如果有）。
2. **respond 命令清单**：从 `CURRENT.md` 的 `respond_commands` 段逐条列出完整可复制的命令（workspace、action、feature 参数已填好，只留 `--note` 让用户填）。
3. **证据路径**：`CURRENT.md` 和 `events.jsonl` 的路径，供深挖。

这是"下一步"本身就是人类决策的场景，命令选项不是附属细节而是核心输出。

## 定位

`/twin` 是本机 xuejiao supervisor harness 的入口。它读取由 Claude agent 维护的 `persona.json`，并用两个隔离的 Claude Code headless session 监督真实项目任务：

- supervisor session：模拟 xuejiao，只生成指令、检查证据、判断继续 / 停止 / 升级；默认无写权限。
- worker session：执行代码修改、测试和验证；按 goal 配置可使用 `acceptEdits` / `bypassPermissions`，安全靠 worktree + hook gate 兜底。

二者不得共用同一个 Claude Code session。supervisor 默认不直接改项目代码。

## 子命令

```text
/twin status --project /abs/path
/twin init --goal-file goal.yaml --persona ~/.xuejiao-twin/persona.json
/twin run --project /abs/path --mode supervised-normal
/twin next --project /abs/path
/twin respond --project /abs/path --action approve_and_continue --feature F-003 --note "复用现有模型"
/twin replan --project /abs/path
/twin loop --project /abs/path --every 5m
/twin validate [--fixtures | .xuejiao-twin/runs/<run_id>]
/twin replay .xuejiao-twin/runs/<run_id>/run.json
```

等价 CLI：

```bash
python3 -m scripts.xuejiao_twin <subcommand> ...
```

## persona 维护边界

- `persona.json` 不由本命令自动生成。
- 更新 persona 时，由 Claude agent 直接读取本机 Cursor / Claude Code 历史，人工确认隐私边界后写入 `~/.xuejiao-twin/persona.json`。
- `persona.json` 不应包含项目名、仓库名、路径名、URL、token 或 secret。

## 稳定性底座（已落地）

worker 默认接近 `bypassPermissions` / `acceptEdits`，安全预算押在六道硬边界：

1. **worktree 隔离**：runtime 默认创建 `worktrees/worker` 并把 worker / ledger planner 锁在该 worktree。`worker_isolation.mode: required` 时项目必须是 git 仓库，否则停在 `needs_human`；base checkout 不干净也直接 `needs_human`，不强行覆盖未提交改动。
2. **PreToolUse hook gate**：runtime 在 workspace 和 worker worktree 写入 `.claude/settings.local.json`，`hook_gate.py` 拦截 force push / reset --hard / git clean / checkout -- / restore / rm -rf / chmod -R 777 / chown -R / terraform apply&destroy / kubectl apply&delete / helm upgrade&uninstall / fly&vercel deploy / npm&pnpm&yarn publish / twine upload / docker push / dropdb / `psql|mysql ... drop`。supervisor 角色额外阻止 Edit/Write/NotebookEdit。Mutating 工具的 `file_path` 一旦落在 worker worktree 之外或 `.git` 目录内，hook 直接 exit 2。
3. **PostToolUse 真证据**：每个 Bash/Edit/Write 之后由同一 hook gate 落 `runs/<run_id>/hook_events.jsonl`，记录 `tool_name` / 已脱敏的 `tool_input` / `returncode` / 响应摘要。bypass worker 自报的 `validation` 不再是唯一来源；run.json 的 `metrics.tool_call_events` 给出实际工具调用数。
4. **SessionStart 上下文锚点**：fresh session（含 `--resume` 静默重启后的新 session）开始时，hook 直接向 Claude Code 注入 `additionalContext`：active role、当前 ledger focus、planning_status、hard rules、schema 名。即便 user prompt / append-system-prompt 被改写也兜底。
5. **PreCompact 标记**：context 压缩前由 hook 写一条 `pre_compact` 事件；replay 高亮 post-compaction 的高风险轮，run.json 暴露 `metrics.compaction_events`。
6. **disallowedTools 基线 + schema 单 JSON 契约**：runtime 向 `claude --disallowedTools` 注入与 hook gate 对齐的 baseline，再叠加 `goal.disallowed_tools[role]`；supervisor / ledger planner / worker 都被要求返回单个 JSON 对象，用 `xuejiao_twin.*.schema.json` 校验；非 JSON 或字段越界即停止。

其它已落地的稳定性特性：

- 两个隔离 headless session（`--resume <session_id>` per turn），raw session id 只存 `session_state.json`，run.json 只保留 hash。
- **角色契约走 `--append-system-prompt`**：role + schema + 硬规则 + scope_out + persona_policy + acceptance / validation_commands 固定在 system prompt，跨轮稳定，prompt cache 友好；user prompt 只剩本轮 delta。
- **每轮 `--setting-sources project,local` + `--strict-mcp-config`**：人类操作员的 `~/.claude/settings.json` hooks 与 `~/.claude.json` MCP 服务器与 worker / supervisor 完全隔离。
- **worker worktree 内 `.claude/CLAUDE.md`**：hard rules + scope_out + worker / planner schema 名 + validation commands。session 重启 / compaction 后契约仍存活。
- **silent `--resume` 检测**：`claude_runner` 比对请求 session id 与 stream-json 首事件返回的 session id，不一致即标 `session_lost=true`，runtime 转 `needs_human` 并写 `session_lost` 事件，避免向重起的 brain 继续灌历史。
- 危险默认（main/master commit/push、外部副作用、worktree 外写入）一律由 runtime 报 `needs_human`。
- `blocked latch`：上轮已 `needs_human` / `agent_failed` / `no_progress` / `failed_validation` / `privacy_blocked` 且无新 `human_response.json` 时，重复 `run` 不会再调用 agent，避免 `/loop` 空跑。
- `validation_gap`：所有 feature completed 但 goal 验证证据不全时，runtime 强制 supervisor 补一个 ledger feature，连续 ≥3 轮无补救才转 `failed_validation`。
- `no_progress` 熔断：focus + ledger + project_evidence 连续 3 轮指纹一致即停。
- 隐私层：`token=`、`bearer ...`、`-----BEGIN ... PRIVATE KEY-----`、含敏感 query 的 URL 触发 redaction；命中 `secret_assignment` / `bearer_token` / `private_key` / `sensitive_url` 直接 `privacy_blocked`。hook gate 对 PostToolUse 的 `tool_input` / 响应摘要单独再脱敏，落盘前不带原文 token。

## 高级开关（goal.yaml）

```yaml
worker_isolation:
  mode: auto | required | off          # 默认 auto；required 强制 git 仓库 + worktree
limits:
  max_turns: 6
  max_wall_minutes: 30
  max_budget_usd: 1.0                  # 透传 claude --max-budget-usd；<=0 直接 budget_exceeded
permission_mode:
  supervisor: plan                     # 推荐 plan，让 agent 层强制只读
  worker: acceptEdits | bypassPermissions
allowed_tools:
  supervisor: [Read, Bash(git status *), Bash(git diff *)]
  worker: [Read, Edit, Write, Bash]    # 近 bypass 默认
disallowed_tools:
  worker: [...]                        # 追加到 runtime baseline
validation_commands: [...]
approval_policy:
  require_human_for: [...]
```

## 演进路线（advanced Claude Code × twin）

A 段（settings/MCP 隔离 / append-system-prompt 角色契约 / worker worktree CLAUDE.md / silent-resume 检测）和 B 段（PostToolUse 真证据 / SessionStart 上下文注入 / PreCompact 标记）已落地，详见上方「稳定性底座」。

剩余路线按收益 / 风险排序：

| 阶段 | 改动 | 收益 |
| --- | --- | --- |
| C1 | runtime 在 worker turn 后自跑 `validation_commands`，作为权威结果；worker 自报降级为 hint。PostToolUse 已能给工具调用 ground truth，C1 把 validation 也归到 runtime 持有 | 阻断 worker 假报 validation |
| C2 | 切换到 `--output-format json` + `--json-schema`，丢掉启发式 JSON parser | 输出契约从我们解析改为 CLI 强约束；envelope 的 `result/total_cost_usd/is_error` 直接可用 |
| C3 | 每轮按 envelope 的 `total_cost_usd` 做 per-turn 预算与异常飙升熔断 | 比 stderr 字符串匹配 `budget` 更准 |
| C4 | supervisor 默认 `--permission-mode plan` | agent 层强制只读，比 disallowedTools 更稳 |
| D1 | `/twin doctor`：体检 claude / git / hook / persona / goal / settings 来源 | 接入新机器更顺 |
| D2 | `statusLine` 命令把 focus / status / blockers 直接渲染到 Claude Code 状态栏 | 不用每次 cat CURRENT.md |
| D3 | Notification hook 在 `needs_human` 时弹通知 | `/loop` 长跑 |
| E1 | `--permission-prompt-tool` 接到本机 MCP server，`supervised-high` 时把 worker 的 novel tool 申请实时交给 supervisor | 把审批流变成 inline 而不是事后 needs_human |
| E2 | 把 supervisor / worker 转成 `.claude/agents/*.md` subagent + Agent SDK | 单 session + Agent 工具，省一对子进程 |

C 段把可信证据从 worker 自报彻底拿到 runtime / CLI 这一层；D 段是 UX；E 段是更深整合。

## 推荐流程

1. 准备 persona：

```text
~/.xuejiao-twin/persona.json
```

2. 在目标项目写 `goal.yaml`，包含 `project_root`、`goal`、`scope_in`、`scope_out`、`acceptance`、`limits`、`allowed_tools`、`approval_policy`、`validation_commands`；可选 `worker_isolation.mode: auto|required|off`、`permission_mode.{supervisor,worker}`。

3. 初始化（`feature_ledger.json` 为空，状态为 `needs_draft`）：

```bash
python3 -m scripts.xuejiao_twin init --goal-file goal.yaml --persona ~/.xuejiao-twin/persona.json
```

4. 先 dry-run，再 supervised。首次 supervised run 让 worker 只读生成 ledger draft；supervisor review 通过后 runtime 写回 ledger 并继续实现：

```bash
python3 -m scripts.xuejiao_twin run --project /abs/project --mode dry-run
python3 -m scripts.xuejiao_twin run --project /abs/project --mode supervised-normal
```

5. 若旧 ledger 质量差或要换方向，可一键 replan，保留 goal/persona/runs 并归档旧 ledger：

```bash
python3 -m scripts.xuejiao_twin replan --project /abs/project
```

6. 回放和验证：

```bash
python3 -m scripts.xuejiao_twin replay .xuejiao-twin/runs/<run_id>/run.json
python3 -m scripts.xuejiao_twin validate .xuejiao-twin/runs/<run_id>
```

7. 日常只看 `CURRENT.md` 和下一步命令；`next` 等价于跑一次 supervised run 后刷新 current：

```bash
python3 -m scripts.xuejiao_twin run --project /abs/project --mode supervised-normal
cat /abs/project/.xuejiao-twin/CURRENT.md
```

8. `needs_human` 时：

```bash
python3 -m scripts.xuejiao_twin respond --project /abs/project --action approve_and_continue --feature F-003 --note "复用现有模型，不新增表"
python3 -m scripts.xuejiao_twin run --project /abs/project --mode supervised-normal
```

`respond --action` 可选：`approve_and_continue` / `request_plan_delta` / `defer_feature` / `stop_session`。`request_plan_delta` 是人工要求 supervisor 在下一轮先输出最小改动清单（文件 + 一行改动 + 验证命令）；空 ledger 自动 draft 是 runtime 生命周期，两者独立。

supervisor 在执行中可通过 `ledger_updates` 要求 runtime 新增或调整 feature；V1 不删除 feature，跳过时使用 `deferred`。

可与 `/loop` 结合成乔布斯式入口：

```text
/loop 5m /twin next --project /abs/project
/loop /twin next --project /abs/project
```

循环体只执行一次 `run` 并刷新 `CURRENT.md`；blocked latch 会在缺少新 `respond` 时保持安静。scheduled routine 也应该遵循同一规则：每次触发只跑一次 `next`，不要手写 shell `while`。

## 故障定位

| 症状 | 可能根因 | 处理 |
| --- | --- | --- |
| `outcome=needs_human, stop_reason="ledger quality is poor"` | 旧 ledger 把 goal 当成单 feature / acceptance 全是全局复制 / feature 描述过宽 | `replan` 重生成 |
| 重复 `run` 不再调用 agent | blocked latch（上轮 needs_human / agent_failed / no_progress / failed_validation / privacy_blocked 未 respond） | 先看 `CURRENT.md` 的 stop_reason，修环境或写 `respond` 后再继续 |
| `outcome=no_progress, stop_reason="same focus and evidence repeated"` | focus + ledger + evidence 连续 ≥3 轮指纹一致 | 看 `progress.md` 找根因，必要时 `respond --action request_plan_delta` |
| `outcome=privacy_blocked` | 输出里出现 `secret_assignment` / `bearer_token` / `private_key` / `sensitive_url` | 检查 worktree 是否误读凭证文件，修 goal scope_in/scope_out |
| `outcome=failed_validation`，所有 focus 都 completed | `validation_gap` 触发，feature 完成但 goal 验证证据缺失 | `respond --action request_plan_delta` 让 supervisor 加补证据 feature |
| `outcome=needs_human, stop_reason="base checkout has existing changes"` | 项目根 untracked / dirty 文件不允许创建 worktree | 在项目根 commit / stash 后重跑 |
| `outcome=needs_human, stop_reason="... session was silently reset by Claude Code"` | runtime 比对发现 `--resume` 后 session id 已变，触发 silent-resume 熔断 | `replay` 看最后几轮，决定 `replan` 重生成 ledger 或重新 init |
| supervisor 反复要求 `draft_ledger` | ledger draft 连续 schema 校验失败 | 看 `events.jsonl` 里 `ledger_draft.schema_errors`；调 `goal.acceptance` / `scope_in` 让特征更可拆 |
| `metrics.blocked_risky_actions > 0` | 文本里出现 force push / production deploy / 新增依赖 等 risk marker | 看 `validation_report.risk_markers`，决定是否 approve / defer |

## 机械验证

本命令对应 fixture 自检（包含 init / dry-run / supervised / replan / blocked latch / 隐私 / hook gate / worktree 等覆盖）：

```bash
python3 -m scripts.xuejiao_twin validate --fixtures
```
