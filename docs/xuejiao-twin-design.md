# xuejiao twin supervisor 设计

## 一句话

`twin` 是运行在 Claude Code 交互模式里的 **xuejiao persona supervisor**，不是 worker harness，也不是新的 agent 平台。

worker harness 已由 dev-rules、项目 `CLAUDE.md`、hooks、preflight、worktree、Claude Code `-p --permission-mode bypass` 承担。`twin` 的价值是代表 xuejiao 持有目标、指挥 worker 多轮推进、验收结果，并只在真正需要真人判断时停下。

## 核心约束

1. **greenfield**：作为全新 `xuejiao-twin` skill / capability 设计，不考虑历史 CLI、workspace、schema 兼容。
2. **plan-first**：启动前必须已有 Claude Code plan mode 产出的 `goal.yaml + feature_ledger`。缺失则不启动。
3. **persona split**：supervisor 使用 `~/.xuejiao-twin/supervisor-persona.md`；worker 只看到 `~/.xuejiao-twin/worker-persona.md`。
4. **interactive supervisor**：supervisor 直接复用 Claude Code 交互模式和原生高级能力，不另起 supervisor `claude -p`。
5. **headless worker**：worker 用 Claude Code `-p --permission-mode bypass` 执行，可自主调研、实现、测试、修复、文档、任务分支 commit/push、创建或更新 PR。
6. **worker 可续跑**：单轮跑不完时用 `claude -p --resume <worker_session_id>` 续同一 worker session；worker session id 只是索引，不是事实源。
7. **无独立 supervisor session**：`/twin` 本身就是 supervisor，不另起、不 resume 另一个 supervisor 会话；交互上下文可辅助体验，但每轮判断必须从单一事实来源重建。
8. **supervisor 验收**：worker 可以声称完成，但完成与否由 supervisor 对照 goal、ledger、diff、测试、preflight、PR/commit 状态判断。
9. **高风险才问人**：低风险和常规风险中间决策由 supervisor persona 直接做最优选择。
10. **单一事实来源**：每类事实只有一个权威载体，禁止重复计划、重复状态、重复规则、重复 persona。

## 角色分工

| 角色 | 做什么 | 不做什么 |
| --- | --- | --- |
| persona supervisor | 持有 goal、判断下一步、纠偏、续跑、验收、升级人工 | 不写代码，不重复 dev-rules，不做危险动作 |
| Claude Code worker | 按 goal/ledger 全力完成交付闭环，产出 diff/test/preflight/PR 证据 | 不作为最终验收者 |
| worker harness | 注入规则、限制工具、运行 hooks/preflight、记录 artifacts | 不判断产品方向 |
| human | 处理真正高风险或目标不清 | 不负责每轮说“继续” |

## 输入契约

`twin` 只接受已准备好的目标工作区：

```text
goal.yaml              # 目标与验收单一事实来源
feature_ledger.json    # 或 yaml；执行计划与证据状态单一事实来源
supervisor-persona.md  # supervisor-only；通常复制自 ~/.xuejiao-twin/supervisor-persona.md
worker-persona.md      # worker-visible；通常复制自 ~/.xuejiao-twin/worker-persona.md
```

`goal.yaml` 只放：

- goal
- acceptance criteria
- non-goals

授权和门禁不进 `goal.yaml`。它们由 Claude Code 原生 `--allowedTools` / `--disallowedTools` / `--permission-mode bypass` / settings / hooks，加上 dev-rules 全局和项目 `CLAUDE.md` 强注入承载。不要再造 `authorized_actions` / `human_gates` 配置层。

`feature_ledger` 只放：

- deliverables
- 顺序或依赖
- 引用的 AC ID，不重复 AC statement
- item-local scope / non-goals
- evidence plan / actual evidence
- current status
- next action

AC 的文字定义只在 `goal.yaml`；`feature_ledger` 只引用 AC ID，并记录 deliverable 如何覆盖这些 AC。

## 最小模板

模板必须少字段、强验收、单一事实来源。能删就删，不为“看起来完整”加字段。

### `goal.yaml`

```yaml
schema_version: 1
id: short-slug
one_liner: 一句话说明要交付的结果

core_goal: |
  只写真正要达成的用户/业务结果，不写过程。

acceptance_criteria:
  - id: AC1
    statement: 可被验证的一条验收条件。AC 文字只在这里定义。
    evidence_type: 预期证据类型，如测试、preflight、页面、API 响应、PR 链接

non_goals:
  - 明确不做什么，防止 worker 扩 scope
```

### `feature_ledger.yaml`

```yaml
schema_version: 1
goal_id: short-slug
items:
  - id: F1
    deliverable: 一个可验收交付物，不写泛泛任务
    scope: 只写本项边界
    covers_ac:
      - AC1
    evidence_plan:
      - 本 deliverable 预计产生的证据，如测试、preflight、diff、PR、运行结果等
    actual_evidence: []
    depends_on: []
    status: pending
    next_action: 给 worker 的下一步最短动作
```

## persona 文件

两份 persona 文件已经是 persona 的单一事实来源，本文不重复写内容：

- `~/.xuejiao-twin/supervisor-persona.md`：supervisor-only；用于纠偏、验收、续跑、human gate 判断。
- `~/.xuejiao-twin/worker-persona.md`：worker-visible；只给 worker 达成 goal 所需的执行偏好。

目标工作区可复制这两份为快照，确保一次 `twin` run 的 persona 不随全局文件变化漂移。

## 运行闭环

```text
while not done:
  1. 读：goal + feature_ledger + supervisor persona + worker evidence
  2. 干：启动或 resume claude -p worker，让 worker 尽量完成闭环
  3. 验：supervisor 对照 AC、ledger、diff、测试、preflight、PR/commit 状态验收
  4. 判：ACCEPTED_DONE / CONTINUE / NEEDS_HUMAN
```

每轮只问三个问题：

1. 完成了吗？
2. 证据够吗？
3. 继续、验收，还是 `NEEDS_HUMAN`？

若未完成且不需要真人，直接继续。不要把 `init / run / next / replay / validate / replan` 暴露成用户心智。

## 单一事实来源

| 事实 | 权威载体 | 说明 |
| --- | --- | --- |
| 目标、AC、non-goals | `goal.yaml` | 不写运行进度，不写工具授权/门禁 |
| 计划、deliverables、AC 覆盖关系、状态、实际证据 | `feature_ledger` | 不重复 AC 文字，不另建平行计划 |
| supervisor persona | `supervisor-persona.md` | supervisor-only；不写项目事实 |
| worker persona | `worker-persona.md` | worker-visible；只写执行偏好 |
| 当前轮次、next instruction、worker session id、terminal status | `supervisor_state.json` | 不复制 ledger 全文；worker session id 只是续跑索引；不记录独立 supervisor session |
| worker 过程与证据 | `runs/<run_id>/*` | 不作为长期计划 |
| 人类门禁回答 | `human_response.json` | 不散落到日志或 CURRENT |
| 人类可读状态 | `CURRENT.md` | 只展示，不承载新事实 |
| 规则 | dev-rules / project `CLAUDE.md` | `twin` 不复制规则 |

同一信息出现在多个载体时，以表中权威载体为准，其它位置只能引用或摘要。

## supervisor 运行模式

`/twin` 或 `/xuejiao-twin` 本身就是 supervisor：它运行在当前 Claude Code 交互会话中，直接复用 Claude Code 原生高级能力：

- `TaskCreate` / `TaskUpdate` / `TaskList`：维护本轮交互可见任务，不作为长期事实源。
- `Agent` subagents：做大范围只读调研、独立 review、Claude Code 功能调研；不替代 worker 主执行链路。
- `Monitor`：监控 worker 日志、测试、CI、PR checks，事件回流给 supervisor 判断。
- `AskUserQuestion`：只在 `NEEDS_HUMAN` 时问一个具体问题。
- `PushNotification`：长任务完成、失败或需要人工时提醒。
- `Read` / `Bash` / `gh`：读取 artifacts、git/PR/CI 状态，形成 supervisor review。
- Claude Code 原生 transcript：作为审计辅助，不作为 goal 或 ledger 事实源。

`/twin` 可以利用当前交互上下文来提高体验，但不能把交互上下文当事实源。每轮判断必须从 `goal.yaml`、`feature_ledger`、`supervisor-persona.md`、`runs/*`、测试/preflight/PR 状态重建。

## worker 调用与续跑

worker 使用 Claude Code headless：

```text
claude -p --permission-mode bypass --allowedTools ... --disallowedTools ... --max-budget-usd ... --output-format stream-json
```

worker prompt 由四部分组成：

1. `worker-persona.md`
2. `goal.yaml`
3. `feature_ledger`
4. supervisor 本轮 `next_instruction`

不要再内嵌第二套 worker persona 或完成标准。

单轮跑不完时保持同一 worker 的 Claude Code 会话连续性：

```text
first turn:  claude -p ...
next turn:   claude -p --resume <worker_session_id> ...
```

规则：

- `worker_session_id` 记录在 `supervisor_state.json`，只作为 Claude Code resume 索引。
- 事实源仍是 `goal.yaml`、`feature_ledger`、`runs/*`、测试/preflight/PR 状态；不能把 worker session memory 当事实源。
- supervisor 每轮给 worker 的 `next_instruction` 必须包含上轮验收结论和当前 ledger gap，避免只依赖会话记忆。
- 若 `--resume` 出现空输出、stale session、session reset 或上下文明显漂移，丢弃该 session id，启动新 worker session，并把必要事实从单一事实来源重新注入。
- 不用 `/continue` 作为自动化依赖；headless 路径使用显式 `--resume <session_id>`。
- 长任务把每轮 worker 控制在预算内，多轮 resume 推进，不单轮无限跑。

## human gate

human gate 的机械边界由 Claude Code 原生 `--allowedTools` / `--disallowedTools` / settings / hooks，加上 dev-rules 和项目 `CLAUDE.md` 承担；本节只保留 supervisor 判断语义，不新增配置层。

`NEEDS_HUMAN` 只用于真正高风险：

- 直接改 `main` / `master` / release 分支、force push、改写已发布历史、merge 到受保护分支。
- 真实架构边界变更，且 plan mode / dev-rules / 项目上下文未明确覆盖。
- 安全边界变更：鉴权、授权、租户隔离、密钥/凭证路径、输入安全模型。
- 数据高风险：迁移、删除、不可逆状态变更、生产数据读写。
- 云资源、IAM、网络、CI/CD 发布链路、生产配置。
- deploy、发布 release、通知外部用户/客户、修改远端共享资源。
- 业务目标不清，且无法从 goal / persona / repo facts 推断。
- 同一问题连续 3 次失败。

默认不需要问人：

- 非 main / 非受保护分支 commit。
- push 到任务分支或 worktree 对应远端分支。
- 创建或更新 PR，包括 draft PR。
- 更新 PR 描述、追加验证结果、同步分支内修复。
- 新增普通代码依赖或 dev/test 依赖，只要理由清晰、锁文件同步、验证通过，且不改变安全/架构/基础设施边界。
- 低风险文档、测试、脚本、局部实现调整。

依赖新增不自动等于高风险。只有改变核心架构、供应链/许可证/安全边界、运行时基础设施、生产部署方式，或引入明显高爆炸半径时，才升级为 `NEEDS_HUMAN`。

## 用户命令面

`xuejiao-twin` 作为全新 skill / capability 设计，不保留旧命令兼容包袱。

只保留极少入口：

```text
/twin <workspace>     # 执行已包含 goal + feature_ledger 的目标工作区；自动 run/review/continue
/twin status          # 查看 goal、ledger 进度、阻塞、人类需要回答什么
/twin respond <text>  # 回答 NEEDS_HUMAN 后继续
```

`init / run / next / replay / validate / replan` 是内部阶段、debug 子命令或高级维护入口，不作为日常用户命令暴露。

## 参考仓库取舍

- `openclaw`：借鉴 run record、scope lock、overall/no-output timeout；不使用其 Gateway/assistant runtime。
- `hermes-agent`：借鉴 session/trajectory/eval/tool registry 思路；不引入巨型 `AIAgent` 或第二套审批/工具语义。
- `evolver`：借鉴 signals、pending gate、review loop、failure -> rule/eval/preflight；不把 `twin` 做成自进化平台。
- `harness`：借鉴状态清晰和 scheduler 不盲跑；不迁移 CI pipeline/runner 体系。

## 实现优先级

P0：greenfield 骨架

- 作为全新 `xuejiao-twin` skill / capability 实现，不做历史 CLI/workspace/schema 兼容。
- supervisor 运行在 Claude Code 交互模式，复用原生工具和高级能力；不另起 supervisor `claude -p`。
- worker harness 归 dev-rules / Claude Code `-p`。
- `goal.yaml + feature_ledger` 缺失时拒绝启动。
- `supervisor-persona.md / worker-persona.md` 作为 persona 单一事实来源。
- `supervisor_state.json` 只存显式 goal-progress 状态，不记录独立 supervisor session。

P1：闭环推进

- 实现 `supervisor_review`：输出 `decision + next_instruction + remaining_gaps + acceptance_evidence + risk_flags`。
- 每轮 worker 后必须 supervisor review，不能把 worker stop 当完成。
- 支持 `CONTINUE` 自动进入下一轮。
- 支持 `fix_drift`、`validate_more`、`mark_ledger_gap`。
- 只有 `NEEDS_HUMAN / ACCEPTED_DONE / failed` 才 terminal。

P2：OPC 固化

- 调度/Monitor 读取 `supervisor_state`，不盲跑。
- no-progress 时改变策略，不直接停。
- accepted_done 前做 final review：goal、AC、diff、tests、preflight、contract/docs。
- 重复失败沉淀为 dev-rules / hook / preflight / eval 候选。

## 最终原则

`twin` 的目标不是多一个流程，而是让人类不再反复说“继续”。

给定 `goal + feature_ledger` 后，persona supervisor 应指挥 worker 干到完整闭环；只有在真正需要 xuejiao 判断的地方，才停下来。
