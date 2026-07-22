# 超级 AI/Agent 团队决策备忘

## 结论先行

不要寻找一个包办一切的“超级 Agent”。真正能长期工作的团队，是把不同能力放在四层里：

```text
治理层 twin
  -> 执行层 CAO + provider CLI
  -> 能力层 Skills + MCP + 项目工具
  -> 验证层 tests + preflight + xj-review + CI
```

人类不做日常接力，只保留高风险审批和架构决策。默认体验仍应接近“说清结果，然后等系统交付”，四层和七个检查点是系统内部结构，不是要求用户每天手工走流程。

## 各高级模式不是同类产品

| 能力 | 最适合负责 | 优势 | 局限 | 在团队中的位置 |
| --- | --- | --- | --- | --- |
| dev-rules `twin` | 目标、计划、轮次监督、证据验收、人类门禁、跨会话重入 | artifact 是事实源；Claude/Codex/Antigravity 共用 host protocol；状态和验收可审计 | 不是 provider 进程平台；当前不做无人值守 supervisor | 治理层 |
| CLI Agent Orchestrator（CAO） | 启动和管理不同 provider CLI、profile、terminal、单轮执行 | 多 provider；统一 HTTP 控制面；隔离 provider 差异 | 不拥有业务目标、AC、最终验收和高风险审批 | 执行层 |
| Claude Dynamic Workflow | 面对模糊或跨仓问题时并行搜集事实、方案、风险和未知项 | 调研 fan-out 快；适合扩大只读证据面 | 结果是研究材料，不是最终决策；规模不受控会烧预算、制造噪声 | 治理层的可选研究加速器 |
| Codex goal（当前 CLI 会话能力） | 让一个明确目标跨多轮持续执行，并记录完成或阻塞状态 | 适合长任务连续推进；目标状态比聊天记忆可靠 | 当前暴露的是单目标连续性机制，不等于多 Agent 调度、持久 artifact 协议或验收治理 | 单 Agent 宿主连续性能力 |
| Skills | 固化可复用的专业工作流和工具入口 | 跨 Claude/Codex/Antigravity 同源分发；领域知识可组合 | prompt 不是权限边界；机械步骤只写 prose 会漂移 | 能力层 |
| MCP / 项目 CLI | 提供实时数据、外部动作和确定性工具 | 接口清楚，可测试，可复用 | 需要认证、权限和契约治理；不能自行决定业务目标 | 能力层 |
| tests / preflight / review / CI | 给出独立证据并阻止不合格变更流出 | 结论可重复；能把“以后注意”变成硬门禁 | 只验证已编码的规则，不能替代产品和架构判断 | 验证层 |

关键判断：`twin` 和 CAO 是互补关系，不应该合并成一个进程。twin 决定“为什么做、做到什么算完、下一轮做什么”；CAO 负责“由哪个 provider/profile 在哪里执行”。

## 四层如何协作

### 治理层

治理层只保留不可化约的判断和状态控制：

- 把用户意图收敛为 `goal.yaml` 的目标、AC 和 non-goals；
- 把交付拆成有边界、有证据预算、有停止条件的 `plan.yaml`；
- 每轮只派发当前最短可验收项；
- 对照 AC、diff、测试和门禁判断 `continue`、`needs_human`、`accepted_done` 或 `failed`；
- 高风险或业务歧义交还人类，不替人批准。

调研可以并行，决策必须单点收敛。Dynamic Workflow 只产出带来源和置信度的 `research.yaml`，twin supervisor 消化后才生成最终 goal/plan。

### 执行层

执行层只关心“把这一轮做好”：

- 默认 Claude headless，保持现有行为；
- 需要 Codex 或 Gemini 时，优先由 `local_cli` 直接调用当前机器的 provider CLI，并由 adapter 每轮固定 sandbox / approval policy；需要远程、多 profile 或其他 provider 时再由 CAO `POST /terminals/run-step` 启动 fresh worker；
- provider、模型、工具和权限归 CAO agent profile；
- 每个可写 worker 必须在独立 worktree 中运行；
- worker 提交结果和证据，但没有最终验收权。

### 能力层

能力层像团队里的专业岗位，不拥有总目标：

| Skill 类型 | 当前代表 | 用法 |
| --- | --- | --- |
| 研发质量 | `xj-review`、`agent-contract-sync` | 审查到 merge-ready；从 live code 生成并守卫 Agent 契约 |
| Git 隔离 | `git-worktree-submodule` | Agent 通过 `wtree.py` 管理 shared-submodule worktree |
| 规则分发 | `dev-rules-fanout` | dev-rules 合并后确定性更新注册项目并开 PR |
| Provider 环境 | `codex0-launcher`、`cc0-claude0-launcher`、`antigravity0-launcher` | 复现代理、出口和 provider 启动环境 |
| 内容生产 | `curator-gzh`、`video-workflow`、`moneywise-workflow`、`copublisher` | 把领域流水线封装为可调用能力 |
| 文档与演示 | `ppt-content-planning`、`pptx`、`inspur-ppt-gen` | 分离内容策划、文件处理和品牌渲染 |
| 能力建设 | `skill-creator`、`plugin-creator` | 新建可复用 skill；需要安装包时再升级为 plugin |

选择原则很简单：可复用判断流程放 skill；可机械化计算放脚本；实时外部数据和动作放 MCP/CLI；安全边界放 sandbox、permissions、hook 和审批门禁。

### 验证层

验证层必须独立于“做事的 worker”：

- 单元、集成和真实 UI e2e 证明行为；
- `scripts/preflight.sh` 汇总可机械门禁；
- `xj-review` 只判断机械脚本覆盖不到的设计和语义残差；
- GitHub Actions 对远端同一 commit 再验证；
- 高风险变更即使内部 review 通过，也不能绕过人类批准。

## 七阶段会不会太形式化

会，如果把七阶段做成七个页面、七次确认或七条人工命令。不会，如果它们只是内部检查点，并按风险自动折叠。

推荐的内部生命周期是：

1. 接收意图：确认真正结果和风险等级。
2. 必要调研：只在信息不足时生成 `research.yaml`。
3. 目标与计划：形成 `goal.yaml` 和 `plan.yaml`，高风险才等待审批。
4. 路由与隔离：选择 backend/profile，创建 worktree。
5. 分项执行：worker 完成一个有界 deliverable 后立即回 supervisor。
6. 验证与纠偏：测试、preflight、review、CI；失败自动回到执行。
7. 交付与沉淀：PR、证据和必要文档；只有长期且代码中查不到的事实才进 memory。

用户默认只感知三个阶段：

```text
定目标 -> 自动执行和纠偏 -> 验收/人工门禁
```

裁剪规则：

- 小改动跳过独立 research，目标和计划在一次交互里完成；
- 普通风险不因“步骤多”增加审批；
- 只有方向不明、跨仓、证据面大或假设代价高时才 fan-out 调研；
- 每个 plan item 都短到可以独立 review，避免一次 worker 长跑吞掉全部阶段；
- 状态、计数、校验和路由由代码自动派生，不让人填流程表。

## CAO 多 provider 如何接入 twin

运行时边界应是 CAO HTTP API，不是 Python import：

```yaml
execution:
  backend: cao
  provider: codex
  agent: developer
```

- 地址读 `TWIN_CAO_BASE_URL`，默认 `http://127.0.0.1:9889`；
- 开启认证时 bearer token 只读 `CAO_AUTH_LOCAL_TOKEN`；
- twin 每轮传入隔离 worktree 的绝对路径并请求 `teardown=true`；
- CAO 每轮 fresh terminal，不把 provider transcript 当跨轮记忆；
- `provider` 和 `agent` 只对 `cao` backend 合法，`claude_headless` 声明它们会被校验拒绝；
- 目标、AC 和 plan item 不得绑定特定模型才成立。

不建议把 CAO 作为 twin 的运行时 submodule。submodule 最多用于固定安装或 bootstrap 版本；真正的依赖必须保持为稳定 HTTP contract，否则 twin 会被 CAO 内部目录、Python 环境和发布节奏绑死。

## `wts`、`wtree.py` 和 skill 怎么分工

不删除 `git-worktree-submodule` skill，也不让 twin shell 调 `wts`：

- 人类在终端使用 `wts`，因为它负责友好的创建、进入目录和启动交互 CLI；
- 普通 Agent 加载 `git-worktree-submodule` skill，按契约调用 `wtree.py` 并绑定 session workdir；
- twin 是程序化调用方，直接消费同一个 `wtree.py --json` 协议；
- 三者共享一个实现，避免各自手写 submodule/worktree 逻辑；
- 创建或 `session-check` 失败必须 fail closed，不能悄悄回到共享 checkout。

所以应该删除的是重复实现，不是 skill。skill 仍负责告诉 Agent 如何正确调用唯一引擎；`wts` 仍是人类入口。

## research 如何接到 goal/plan

数据流必须是单向的：

```text
Dynamic Workflow 只读调研
  -> research.yaml（事实、来源、置信度、选项、风险、未知项）
  -> supervisor 取舍和消歧
  -> goal.yaml（目标、AC、non-goals）
  -> plan.yaml（deliverable、依赖、证据预算、停止条件、执行路由）
```

`research.yaml` 不直接升级为计划，也不能覆盖人类已批准的目标。这样既接住并行调研的广度，又防止多个 researcher 各自产生一套互相冲突的目标。

默认由当前 host supervisor 判断是否需要 research。Claude 保留 `/twin "<goal>"` 薄适配；Codex/Antigravity 可用自身 plan 能力形成 artifact 后进入 `twin run`。显式 research 只用于需要保留调研证据或分阶段决策的任务，不应成为所有任务的强制仪式。

## 当前 Codex CLI 怎么参与

当前 Codex 会话直接运行：

```bash
twin run <workspace> --supervisor host/codex --json
```

CLI 在 instruction/review 判断点返回 bounded context、revision、one-time token 和 exact submit command；Codex 做完判断后按 payload 提交，再调用 `next_command`。确定性 worker turn、recovery、schema 和 artifact mutation 都在 Python 中，不复制 Codex 专属状态机。Claude 和 Antigravity 只替换 host route。

## 本轮已落地与未落地

已落地：

- `research.yaml` schema、校验、bootstrap 和文档；
- `plan.yaml.execution` worker backend 路由；
- Claude headless 默认 backend、主流 provider 的 `local_cli` backend 和 CAO 多 provider backend；
- CAO URL/token 契约、fresh terminal、teardown 和证据记录；
- twin 复用 `wtree.py`，worktree 失败 fail closed；
- live CLI/schema 生成 Agent contract，并由 preflight/CI 检查漂移。
- 独立 `twin` executable、host supervisor driver 和 revision/token 防重放；
- Codex/Claude/Antigravity 共用 self-describing action protocol；
- Claude `/twin` 薄适配和 Codex/Antigravity 生成导航。

仍不在本轮：

- CAO 无人值守 supervisor backend；
- CAO bootstrap submodule（只有确认需要可重复安装时再加）。

## 下一步优先级

1. 先用当前通用 CLI + host supervisor 跑真实普通任务，验证三端重入体验。
2. 只有出现明确无人值守需求和 ROI 后，再审批 CAO supervisor。
3. provider 扩展继续优先落 `local_cli` adapter；不要为每个 provider 复制 supervisor 实现。

衡量成功的标准不是“接了多少 Agent”，而是：用户是否只需表达一次目标；系统是否能自动推进、验证和重入；失败是否停在清楚且可恢复的位置；高风险是否准确回到人类。
