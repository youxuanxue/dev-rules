运行 xuejiao persona supervisor，驱动 Claude Code worker 完成目标。

$ARGUMENTS

## 用户命令面

```text
/twin "<one-line goal>"
/twin research "<one-line goal>"
/twin plan "<one-line goal>" [--research <research.yaml>]
/twin <workspace>
/twin status [workspace]
/twin respond <text>
```

## 用户命令短路

`status` / `respond` 是 terminal short-circuit：不进入主路径、不进入 plan mode、不调用 Agent / Read / Grep / Glob，不读取 workspace artifact。最多执行一次 Python 子命令；最终回答只能逐字转发 Python stdout，失败时只转发 stderr 摘要和退出码。

如果参数以 `status` 开头，只执行：

```bash
PYTHONPATH="$DEV_RULES" python3 -m scripts.twin status [--workspace <workspace>] [--json]
```

其中 `/twin status <workspace>` 映射为 `status --workspace <workspace>`。禁止读取 `research.yaml` / `goal.yaml` / `plan.yaml` / `CURRENT.md` / `runs/*` / `reviews/*` 正文，禁止展开证据文件内容，禁止生成额外总结。worker 活性/停滞诊断由 `status_workspace`（`scripts/twin/workspace.py:worker_running_diagnostics`）在 Python 侧确定性算好，作为 `display.worker.{state,note,last_activity_seconds,events_bytes}` 字段随 stdout 返回；status 逐字转发即可，**禁止模型自己从 artifact 的 mtime / 大小重新派生活性**——那是已经机械化的判断，重算只会引入不确定。status 保持只读、不修复状态，是固定短输出，超过 Python stdout 的内容一律不是 status 命令职责。

如果参数以 `respond` 开头，只执行：

```bash
PYTHONPATH="$DEV_RULES" python3 -m scripts.twin respond <text>
```

可传 `--workspace <workspace>`，但默认依赖最近一次 `/twin <workspace>` / `/twin status <workspace>` 记录的 active workspace。active workspace 按当前项目 cwd 隔离（指针文件位于 `~/.claude/twin-active-workspaces/<sha256(cwd)[:16]>`），切换项目目录不会读到上一个项目的工作区；指向的 workspace 已被删除时会立即报「no longer exists」错误，不会向下游静默漂移。禁止读取或复述 `human_response.json` 正文。

## 主路径

`/twin "<one-line goal>"` 是默认 bootstrap。当前 Claude Code supervisor 先判断 repo facts 是否足够：普通目标直接按 plan mode 草拟 `goal.yaml + plan.yaml`；只有跨仓、方向不明、证据面很大或高代价假设较多时，才内部执行下述 research 路径。不要把 research 变成每个任务的必经阶段。

`/twin research "<one-line goal>"` 是可选只读调研入口。supervisor 启动 bounded Dynamic Workflow 做并行调查，只允许 Read / Grep / Glob / WebSearch / WebFetch 等只读动作；禁止编辑代码、创建或切换分支、提交、发布以及外部 mutation。唯一允许的写入是 `.twin/research/<slug>/research.yaml` 研究产物，字段必须包含带来源和置信度的 facts、options、risks、unknowns、recommended_direction，并调用 `python3 -m scripts.twin validate <research.yaml>` 校验。Dynamic Workflow 的规模设置只是建议值，supervisor 必须按问题边界限制 fan-out，并对影响最终范围的关键结论回到原始代码或官方来源二次核验。

`/twin plan "<one-line goal>" --research <research.yaml>` 消费已验证研究产物。research 只提供证据，不直接决定目标；当前 supervisor 负责消歧、取舍、明确 non-goals 和 AC，再亲自草拟 `goal.yaml + plan.yaml`。plan 必须在 worker 启动前拆成短交付：多 AC 不得塞进单个 item；每个 item 要写清边界、证据预算、停止/转 review 条件；已知门禁缺口用 `blocked` / `deferred` + `blocked_reason` 表达；最终验收/summary/preflight 项必须依赖前置交付项。

supervisor 用 `AskUserQuestion` 请求确认 goal、AC、non-goals 和 plan。确认后调用 `python3 -m scripts.twin bootstrap --workspace <ws> --goal-file <goal.yaml> --plan-file <plan.yaml> [--research-file <research.yaml>]` 写入并校验 workspace，再进入执行闭环。`python3 -m scripts.twin scaffold "<goal>" --json` 只作为最小 scaffold fallback，不代表真实 planning。

`/twin <workspace>` 启动或 resume 已准备好的 workspace。进入主路径先调用 `python3 -m scripts.twin next --workspace <ws> --json`，按 artifact state 决定是生成 supervisor instruction、等待 worker、review 当前 run、启动下一轮 worker、询问真人还是结束；不得依赖上一次交互会话的记忆。每轮 supervisor 必须自循环：

```text
supervisor-context → 写 next_instruction → worker-turn → review-context → 写 review JSON → review → continue 自动下一轮
```

只在 `accepted_done` / `needs_human` / `failed` / bounded `worker_quiet_timeout` 停下；`review_required` / `continue` 不是用户停点，`/twin <workspace>` 必须从 artifact 自动恢复 review 或下一轮，不能让用户反复说“继续”。`worker_running` 若已出现 run artifact 则进入 review，若无 artifact 则恢复 fresh worker turn，若仍 active/quiet 则调用 bounded `watch` 后再回到 `next`。worker stop 不是完成；后台 worker 完成通知后的确定性重入口也是 `/twin <workspace>`。

## Worker 执行禁令（防 oversized resume 死循环）

worker **只能**通过同步 Bash 调用：

```bash
PYTHONPATH="$DEV_RULES" python3 -m scripts.twin worker-turn --workspace <ws> --instruction "<supervisor-authored>" --json
```

禁止用 Claude Code daemon / 后台 slash worker / `Task` 后台模式 / `--fork-session --resume <*.jsonl>` 交互式 transcript / 任何非 `worker-turn` 路径代替 worker。supervisor 在当前交互会话里写 instruction 与 review；**不得**把 supervisor 会话 fork 成 daemon worker 去“代跑 worker”。worker 活性与完成以 `runs/<run_id>/run.json` 与 `events.jsonl` 为准，不以 `~/.claude/jobs/*` 或 daemon roster 为准。若 `worker-turn` 因 oversized body / body-guard 拒载，`scripts.twin` 会清掉 `worker_session_id` 并 fresh retry；不得手工 `--resume` 旧 session 绕过该机制。

### worker git 隔离（默认开）

每个 workspace 的 worker 默认在一个**独立、带稳定分支的 git worktree** (`<repo-parent>/<repo>-twin-<workspace-id>`) 中运行。`scripts/twin/worktree.py` 只调用 `$git-worktree-submodule` 的 `wtree.py` JSON 契约，以当前已确认 `HEAD` 为 base，复用其 shared-submodule 修复和 `session-check`。创建或校验失败时 worker turn fail closed，不得回退共享 checkout。workspace 到 terminal 状态后只清理没有未保存业务改动的 worktree；有改动则保留。`TWIN_WORKTREE_ISOLATION=0` 仅用于明确的只读调试，禁止用于可写 worker。普通交互式 Agent 仍加载 `$git-worktree-submodule`；人类仍使用 `wts`。

### worker backend

`plan.yaml` 未声明 `execution` 时继续使用 Claude headless。需要直接使用本机已安装的 provider CLI 时选择 `local_cli`；CAO HTTP 仍保留为远程、多 profile 和并发场景，不要求本机启动 CAO server：

```yaml
execution:
  backend: local_cli
  provider: codex # claude | codex | gemini
```

`local_cli` 运行器直接调用本机 `claude`、`codex` 或 `gemini` 的非交互模式；Codex 使用 `workspace-write` + `approval_policy=never`，Gemini 使用 `approval-mode=yolo`，每轮仍在 twin 隔离 worktree 中运行。Claude 的 `local_cli` provider 复用现有 headless stream/budget/resume 语义。provider 可用性只读诊断：

```bash
PYTHONPATH="$DEV_RULES" python3 -m scripts.twin doctor --json
```

直接 CLI 不支持 Claude 原生美元预算的 provider，在显式设置 `--max-budget-usd` 或 `TWIN_WORKER_MAX_BUDGET_USD` 时会 fail closed；不要把未支持的预算语义当作已生效。

CAO backend 继续通过稳定 HTTP 控制面执行，不从 CAO submodule import 内部模块：

```yaml
execution:
  backend: cao
  provider: codex
  agent: developer
```

CAO 地址由 `TWIN_CAO_BASE_URL` 配置，默认 `http://127.0.0.1:9889`；启用 CAO auth 时从本机 `CAO_AUTH_LOCAL_TOKEN` 读取 bearer token，secret 不进入 plan 或 run artifact。CAO 每轮调用 `POST /terminals/run-step`，传入隔离 worktree 的 `working_directory` 并使用 `teardown=true`。`agent` 必须是 `cao profile list` 实际可见的 profile；provider 模型、工具和权限由该 CAO agent profile 管理。CAO `run-step` 当前没有费用预算字段，`--max-budget-usd` / `TWIN_WORKER_MAX_BUDGET_USD` 只适用于 Claude headless，在 CAO backend 下显式设置会 fail closed。

## workspace 契约

`<workspace>` 必须包含 `goal.yaml` 与 `plan.yaml`，可选包含已验证的 `research.yaml`。workspace 内禁止出现 `supervisor-persona.md` / `worker-persona.md`；persona 直接读 `$DEV_RULES/personas/*.md`。字段定义见 `docs/twin-design.md`。

## status 展示

`/twin status [workspace]` 是人类状态面：展示目标、可读状态、当前 item、轮次、下一条命令和必要证据路径；`worker_running` 时额外展示一行 compact worker 诊断——其 `state`（starting/active/quiet/stale_no_artifacts/completed_artifact_present）与 `note` 均来自 Python 计算字段，非模型推断。`--json` 保留机器字段。status 只读，不重写 workspace artifact。

## needs_human 展示

state 或 review 落到 `needs_human` 时，优先用 `AskUserQuestion` inline 问一个具体问题，给一段背景和推荐选项。证据路径只作为辅助：`CURRENT.md` / `supervisor_state.json` / `runs/<run_id>/run.json::review`。不要要求用户读 JSON 才能回答。`/twin respond <text>` 是唯一解除该门禁的用户命令，成功后会写入 `human_response.json` 并在 `workspace_events.jsonl` 记录不含回答正文的审计事件。

## 输出风格

一个人类可读状态、一条下一步、必要证据路径；不复述 runbook 或 design。supervisor 每轮内部子命令调用顺序见 `docs/twin-supervisor-runbook.md`。
