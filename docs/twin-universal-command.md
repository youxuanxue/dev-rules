---
status: proposal
date: 2026-07-21
scope: twin universal command and supervisor runtime
---

# twin 通用命令架构

## 一句话

把 `twin` 建成与 Claude、Codex、Antigravity 无关的独立 CLI 和状态协议；各 Agent 端只保留薄适配器，`local_cli` 负责当前机器的主流 provider，CAO 负责远程、多 profile 和长尾 provider，twin 只负责目标、计划、监督、验收和人类门禁。

## 背景

当前 twin 已经具备一组大体通用的 Python 原语：

```text
status / next / watch / respond
supervisor-context / worker-turn
review-context / review
scaffold / bootstrap / validate
```

但完整监督闭环仍写在 Claude Code 的 `commands/twin.md` 中：当前 Claude 交互会话读取 context、生成 worker instruction、审查 run evidence，并写出 review JSON。因此 `/twin` 是 Claude-only；Codex 和 Antigravity 虽能作为 CAO worker，却不能直接复用同一用户命令面充当 supervisor。

需要抽离的是 supervisor 的宿主入口和驱动协议，不是重写 twin 状态机，也不是重新实现 CAO。

## 目标

1. 人类在 Claude、Codex、Antigravity 或普通 shell 中使用同一组 `twin` 命令。
2. 当前 Agent 会话可以作为交互式 supervisor，不再固定为 Claude。
3. 需要无人值守时，可以通过 CAO 启动独立 supervisor Agent。
4. goal、plan、run、review、human gate 和 worktree 行为在不同 provider 下保持一致。
5. 各端不复制 supervisor runbook，所有确定性状态变化仍由 Python 校验和执行。
6. 保留现有 `/twin` 使用方式，迁移期间不破坏已有 workspace。

## 非目标

- twin 不重新实现 CAO 的 provider、terminal、session、MCP 或多 Agent 通信。
- twin 不取代 `wtree.py` / `wts` 的 worktree 能力。
- 不要求 research、plan、run 等步骤全部成为每个任务的必经流程。
- 不把 skill 当作安全边界；权限、sandbox、hooks 和 preflight 仍由各自权威层负责。
- 第一阶段不追求无人值守、跨机器调度、Web UI 或 Agent swarm。

## 总体架构

```text
Claude /twin command ---------+
Codex twin skill -------------+
Antigravity twin skill -------+--> twin CLI / driver protocol
Shell / CI -------------------+           |
                                          +-- workspace/state engine
                                          +-- supervisor backend
                                          +-- worker backend
                                          +-- validation/worktree/preflight
                                                       |
                                                       +--> CAO HTTP API
                                                              |
                                                              +-- Codex CLI
                                                              +-- Claude Code
                                                              +-- Antigravity CLI
                                                              +-- other providers
```

### 责任边界

| 组件 | 责任 | 不负责 |
| --- | --- | --- |
| `twin` CLI | 用户命令、状态驱动、artifact 契约、重入 | provider 终端实现 |
| twin state engine | goal/plan/run/review 状态迁移和校验 | 模型判断 |
| supervisor backend | 生成本轮 instruction 和 review judgment | 绕过 schema 或人类门禁 |
| worker backend | 在指定 worktree 执行本轮交付 | 最终验收 |
| shared twin skill | 教当前宿主 Agent 如何履行 supervisor 协议 | 持久化权威状态 |
| CAO | 启动、观察、停止不同 provider 的 CLI Agent | twin 目标和验收状态机 |
| `wtree.py` | worktree 创建、复用、检查和安全清理 | supervisor 决策 |
| tests/preflight/review | 机械证据和独立验证 | 替代业务或高风险审批 |

## 通用命令面

唯一可执行入口放在 `global/bin/twin`，由 `sync.sh` 分发到 `~/.local/bin/twin`。入口只定位 `DEV_RULES` 并调用版本化 Python 模块，不承载业务判断。

```bash
twin research "<one-line goal>"
twin plan "<one-line goal>" [--research <research.yaml>]
twin run <workspace> [--supervisor <route>]
twin status [workspace] [--json]
twin respond [workspace] "<answer>"
twin validate <artifact-or-workspace>
twin doctor
```

`twin doctor` 只做只读诊断，至少报告：

- dev-rules 和 persona 源是否可定位；
- workspace schema 和 Python runtime 是否可用；
- `wtree.py` 契约是否可用；
- worker/supervisor 所需 provider 是否安装；
- CAO 地址是否可达以及认证是否配置；
- 选定 CAO agent profile 是否存在；
- 当前 repo 是否满足 worktree 前置条件。

## Supervisor 模式

### Host supervisor

```bash
twin run <workspace> --supervisor host
```

当前 Claude、Codex 或 Antigravity 会话就是 supervisor。共享 `twin` skill 驱动 CLI 协议：

```text
next
  -> supervisor_instruction
  -> host 生成 instruction
  -> worker_turn
  -> review_run
  -> host 生成 review JSON
  -> apply_review
  -> continue / needs_human / done / failed
```

Host 只负责不可机械化的判断：

- 调研结论的取舍；
- goal、AC、non-goals 和 plan 拆解；
- 下一轮 worker instruction；
- 对 run evidence 的验收判断；
- 是否需要人类决策。

Python 继续负责：

- context 构造；
- schema 校验；
- 状态版本和 transition 校验；
- run/review artifact 写入；
- worktree 和 backend 调用；
- status、watch、重入和 human response 记录。

Host 模式是第一阶段默认模式。它能让当前 Codex CLI 直接监督 twin，同时不必先解决无人值守 supervisor 的全部可靠性问题。

### CAO supervisor

```bash
twin run <workspace> --supervisor cao/codex/twin_supervisor
```

无人值守模式下，twin controller 根据同一 driver protocol 调用 CAO `POST /terminals/run-step`，让独立 supervisor Agent 生成结构化 instruction 或 review。

要求：

1. supervisor 与 worker 使用不同 CAO agent profile，职责和权限不可混用。
2. supervisor 默认只读 repo 和 artifacts；需要落 review 时只通过 twin 的结构化提交入口写状态。
3. supervisor 输出必须通过 schema、workspace version 和当前 action token 校验，过期结果不得应用。
4. 每次调用可以 fresh terminal；跨轮事实只从 twin artifacts 重建。
5. `needs_human`、高风险审批和架构决策不得由无人值守 supervisor 自行放行。
6. CAO 故障、超时或 provider 不可用必须停在可重入状态，不得把失败解释为完成。

### Supervisor route 的归属

Worker 路由继续由 `plan.yaml.execution` 保存：

```yaml
execution:
  backend: cao
  provider: codex
  agent: twin_codex_worker
```

Supervisor route 是本次运行的控制面选择，不改变 goal 或 plan 语义。CLI 解析后的 supervisor backend identity 写入 `supervisor_state.json`，用于中断后恢复和防止静默换 provider。恢复时 route 不一致应 fail closed，除非使用显式 replace/migrate 操作。

## Driver protocol

通用化不能只是把 `/twin` 改名为 shell 脚本。必须把当前写在 Claude command prose 中的循环收敛成稳定协议。

建议以现有 `next` 为基础，定义有限 action 集：

```text
need_plan
supervisor_instruction
worker_turn
watch_worker
review_run
ask_human
done
failed
```

每个需要 supervisor 判断的 action 都返回：

- `workspace` 和状态版本；
- 当前 goal/plan item；
- 有界 context；
- 期望的输出 schema；
- 一次性 action token；
- 可调用的确定性提交命令。

提交 instruction/review 时必须同时提交状态版本和 action token。这样可以阻止后台旧 supervisor、重复回调或中断前结果覆盖新状态。

Host skill 和 CAO supervisor backend 消费完全相同的 action，不各自实现一套状态推断。

## 多端适配

### Claude Code

保留 `/twin`，但 `commands/twin.md` 最终缩成薄适配器：加载共享 twin skill、转发参数、调用 `twin` CLI。Claude 不再拥有独立 supervisor runbook。

### Codex CLI

通过三端共享的 `twin` skill 使用 host supervisor 模式。skill 是工作流入口，实际状态和 mutation 仍由 `twin` executable 承担。

Codex plugin 可作为后续安装和分发容器，但不是第一阶段前置条件；不要为了模拟 Claude slash command 再复制一份命令实现。

### Antigravity CLI

使用与 Codex 相同的 shared skill 和 executable。只允许在适配器中处理宿主特有的交互能力，不复制状态机和判断规则。

### Shell / CI

- 人工 shell 可直接使用 status、validate、doctor 和 respond。
- 完全无人值守的 `run` 必须显式选择 CAO supervisor route。
- `--supervisor host` 不得在没有 Agent host 的 CI 中伪装成自动模式。

## SSOT 与文件归属

| 内容 | 唯一源 |
| --- | --- |
| 可执行入口 | `dev-rules/global/bin/twin` |
| 状态机和 schema | `dev-rules/scripts/twin/`、`dev-rules/schemas/` |
| twin 架构和协议 | `dev-rules/docs/` |
| shared twin skill | `agent-skills/twin/SKILL.md` |
| Claude 命令薄适配器 | `dev-rules/commands/twin.md` |
| supervisor/worker persona | `dev-rules/personas/` |
| provider 进程管理 | CAO HTTP API 和 CAO agent profiles |
| worktree 实现 | `git-worktree-submodule` 的 `wtree.py` |

禁止在 Claude command、Codex skill、Antigravity skill 中各自保留完整 runbook。共享 skill 只保留 supervisor 判断协议；可计数、解析、状态派生、路径选择、校验和 artifact mutation 必须在 Python 中。

## 安全和治理

1. twin supervisor review 只是内部轮次裁决，不是高风险变更的最终批准。
2. 高风险信号必须进入 `needs_human` 和既有 PR/`docs/approved` 审批通道。
3. CAO 继续通过 HTTP contract 使用，不 import 内部模块；submodule 只能服务于安装复现，不能成为运行时耦合。
4. 所有可写 worker 必须运行在 twin 隔离 worktree；创建或检查失败时 fail closed。
5. CAO 地址读取 `TWIN_CAO_BASE_URL`；启用认证时 token 只读取 `CAO_AUTH_LOCAL_TOKEN`，不得写入 plan、state、run 或日志，且非 loopback 地址必须使用 HTTPS。
6. 非交互 provider profile 不得配置会等待人工终端输入的 approval 模式。
7. skill 约束不是硬权限。真正的权限边界来自 provider sandbox、permissions、hooks、worktree 和外部审批。
8. 任一 backend 返回的自由文本都不能直接改变状态，必须转成 schema 化 decision 并通过校验。

## 三阶段落地

不采用面向用户的七阶段流程。实现只分三个可独立交付的工程阶段。

### 阶段一：通用 CLI 和 host supervisor

- 新增 `global/bin/twin` 和 `twin doctor`。
- 把现有 Claude supervisor runbook 提炼为 `agent-skills/twin` shared skill。
- 定义并测试 driver action/token 协议。
- 让 Claude `/twin` 成为薄适配器。
- 在 Codex 和 Antigravity 中跑通 host supervisor fixture。

完成标志：同一个 workspace 可以由 Claude 或 Codex host 监督，worker backend 和最终 artifacts 一致。

### 阶段二：CAO supervisor

- 抽象 supervisor backend。
- 建立最小只读 `twin_supervisor` CAO profile。
- 实现 instruction/review 的结构化 CAO 调用。
- 增加 timeout、stale result、重复回调和中断重入测试。
- 让 shell/CI 可以显式启动无人值守 run。

完成标志：没有交互宿主时，CAO supervisor 可以推进普通风险 workspace，并在 human gate 可靠停下。

### 阶段三：收敛和硬门禁

- 删除 Claude-only 的重复 runbook，只保留兼容入口。
- 更新 README、宪法和 agent contract，把 twin 标记为三端通用。
- preflight 检查 executable、skill、command adapter 和 schema 是否漂移。
- 增加跨 provider contract tests 和真实最小 smoke test。
- 保留旧 `/twin` workspace 的兼容读取和明确迁移诊断。

完成标志：三端只消费同一 CLI、skill 和 artifact contract，任一端的适配器删除都不会损坏核心 workspace。

## 验收标准

1. `twin status`、`validate`、`doctor` 可从普通 shell 独立运行。
2. Claude、Codex、Antigravity 的 host supervisor 都消费同一 shared skill 和 driver actions。
3. Codex host 能从已有 workspace 完成 instruction、worker turn、review 和 continue/done 闭环。
4. CAO supervisor 能在中断后仅靠 artifacts 恢复，不依赖旧会话 transcript。
5. worker backend 可在 `claude_headless` 与 `cao/<provider>/<agent>` 间切换而不改变 goal/AC。
6. `needs_human` 在所有入口都停在同一状态，`twin respond` 后可重入。
7. stale supervisor decision、重复 review、错误 workspace version 和 provider drift 均 fail closed。
8. worktree、CAO auth、persona 只读、schema 和 preflight 门禁全绿。
9. 现有 `/twin <workspace>` 继续工作，迁移不要求重建 workspace。

## 需要审批的后续决策

以下决策在实施前需要明确：

1. shared skill 的正式名称使用 `twin` 还是 `twin-supervisor`；推荐 `twin`，用户入口更直接。
2. host supervisor 是否允许在一次调用中自动循环到 terminal；推荐允许，但 `needs_human` 和 bounded watch 必须立即返回宿主。
3. CAO supervisor 第一批允许的 provider；推荐先只支持 Codex，稳定后再扩展。
4. supervisor backend identity 的 state schema 变更和旧 workspace 兼容策略。

## 最终原则

通用 twin 不是“所有端都拥有一个同名 slash command”，而是“所有端消费同一个 CLI、同一个 supervisor 协议和同一套 artifacts”。宿主负责判断，twin 负责确定性状态，CAO 负责执行，验证层负责证据，人类保留真正的高风险决策权。
