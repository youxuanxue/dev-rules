# Spec 方法调研：OpenSpec 与 Spec Kit

> 目的：为 `dev-rules` 判断是否吸收 spec-driven development（SDD）做法提供依据。结论不是引入某个工具，而是筛出能降低 agent 失控、需求漂移与 review 成本的机制。

## 资料来源

- OpenSpec 官网：https://openspec.dev/
- OpenSpec 概念文档：https://github.com/Fission-AI/OpenSpec/blob/main/docs/concepts.md
- OpenSpec OPSX 工作流：https://github.com/Fission-AI/OpenSpec/blob/main/docs/opsx.md
- Spec Kit 文档：https://github.github.com/spec-kit/
- Spec Kit quickstart：https://github.github.com/spec-kit/quickstart.html
- Spec Kit 安装说明：https://github.github.com/spec-kit/installation.html

## 核心判断

`dev-rules` 已经有自己的约束骨架：`docs/approved/` 承担高风险设计基线，`.testing/user-stories/` 承担验收与测试对齐，`scripts/preflight.sh` 承担机械门禁。OpenSpec 和 Spec Kit 的价值不在于替换这些路径，而在于补强一个薄弱点：**变更意图在实现前、实现中、实现后如何被持续校准**。

建议吸收：

- OpenSpec 的“当前行为 spec + change delta + archive”模型，用来减少改动前后语义漂移。
- Spec Kit 的“constitution → specify → plan → tasks → implement”分层，用来约束 agent 不把需求、技术方案和任务清单混成一坨。
- 两者都强调 spec 放进仓库、随 PR review、跨会话保留上下文，这与 `dev-rules` 的单一事实来源一致。

不建议吸收：

- 不默认要求每个需求都走完整 SDD 流程；这会违背 `product-dev.mdc` 的默认单 PR、按风险升级原则。
- 不把 OpenSpec/Spec Kit CLI 作为 `dev-rules` 的基础依赖；不同项目的 agent、语言栈、CI 环境差异较大。
- 不把 spec 当成“写更多文档”的理由；只有能参与 review、验证或后续归档的文档才值得落盘。

## OpenSpec：可借鉴点

OpenSpec 的中心抽象是：

- `specs/`：当前系统行为的 source of truth，按能力域组织。
- `changes/`：待实现变更，每个 change 带 proposal、delta specs、design、tasks。
- delta spec：用 ADDED / MODIFIED / REMOVED 描述“相对当前行为变了什么”。
- archive：变更完成后，把 delta 合并回主 spec，并保留 change 历史。

对 `dev-rules` 的启发：

1. **把“当前行为”和“本次变更”分开**  
   现有 `docs/approved/` 更像高风险设计基线；OpenSpec 的 delta 模型适合补足常规功能变更中的“这次到底改了哪些行为”。

2. **review intent，不只 review code**  
   PR review 可以先看 delta：新增了什么 requirement、修改了什么 scenario、删除了什么行为，再看代码是否匹配。

3. **归档不是结束文档，而是更新事实源**  
   高风险设计文档 merge 后会成为基线；OpenSpec 提醒我们，常规行为 spec 也应在实现后变成“当前事实”，否则 spec 只会成为一次性计划。

4. **轻量优先**  
   OpenSpec 明确避免长周期瀑布式 upfront design。`dev-rules` 可借鉴“够用就进入实现，变化时更新 spec”的态度。

## Spec Kit：可借鉴点

Spec Kit 的中心抽象是：

- `constitution`：项目治理原则，影响后续 spec、plan、task。
- `specify`：只描述 what / why，不提前绑定技术栈。
- `clarify` / `checklist` / `analyze`：显式处理歧义与一致性。
- `plan`：技术选型、数据模型、契约、quickstart。
- `tasks` / `implement`：把计划转为可执行任务，并让 agent 按依赖实施。

对 `dev-rules` 的启发：

1. **先治理原则，再需求生成**  
   `global/CLAUDE.md` 和 `rules/*.mdc` 已经承担 constitution 角色；可在命令提示中更明确地把它们作为需求拆解、计划、实现的上游约束。

2. **spec 阶段禁止偷跑技术方案**  
   `/user:decompose` 已要求先聚焦过滤和风险判断；可进一步吸收 Spec Kit 的边界：需求 spec 只写 what / why，技术 how 放到 plan 或高风险设计文档。

3. **歧义处理需要实体化**  
   Spec Kit 把 clarify/checklist/analyze 做成独立动作。`dev-rules` 可以要求 agent 在常规风险以上的需求里记录“待澄清项如何被消解”，避免把猜测伪装成结论。

4. **任务清单要尊重依赖与并行**  
   `/user:decompose` 已有依赖图和引擎路由；Spec Kit 的任务实施思路可以加强“任务必须可执行、可验证、可按依赖推进”。

## 对比

| 维度 | OpenSpec | Spec Kit | 对 `dev-rules` 的取舍 |
| --- | --- | --- | --- |
| 主要目标 | 维护活的行为 spec，并用 delta 管理变更 | 用结构化流程把想法推进到实现 | 以 OpenSpec 的 delta 补行为漂移，以 Spec Kit 的阶段分工补 agent 纪律 |
| 信息结构 | 当前 specs + changes + archive | constitution + feature specs + plan + tasks | 保留 `docs/approved/` / Story / preflight，新增轻量 spec 语义即可 |
| 适用场景 | Brownfield、跨会话、review 意图 | Greenfield、复杂规划、多步生成 | 默认不强制；常规风险可轻量使用，高风险才完整展开 |
| 风险 | 规格库维护成本、archive 遗漏 | 流程变重、agent 机械套模板 | 用风险分级触发，不做全量迁移 |
| 最值得借鉴 | Delta spec 和 archive | Constitution gate、clarify、plan/tasks 分层 | 形成 `spec delta → test/story → preflight` 的闭环 |

## 建议落地形态

### 默认路径：轻量 spec delta

适用于常规风险需求，目标是让 reviewer 快速知道行为变化：

```text
Intent → Spec Delta → Implementation → Tests → Summary
```

建议落盘位置：

- 不新增强制目录。
- 当需求复杂到一句 PR summary 说不清，但又未命中高风险时，可写 `docs/spec-delta-<slug>.md`。
- 内容只保留：背景、ADDED/MODIFIED/REMOVED 行为、关键 scenario、验证命令。

### 高风险路径：沿用 docs/approved

适用于 `product-dev.mdc` 已定义的高风险变更：

```text
需求分析 → docs/approved 设计基线 → 原型/实现 → Story/Test 对齐 → preflight → review
```

可借鉴 OpenSpec 的 delta 语言，但文件仍放在 `docs/approved/`，并继续使用现有 frontmatter 与审批规则。

### Story/Test 路径：把 spec 变成可测行为

Spec 不应停在自然语言。进入完整 Story 路径时：

- requirement/scenario 对应 Story AC。
- AC 对应 Linked Tests。
- preflight 或项目级 `verify_quality.py` 检查引用不漂移。

## 推荐新增规则口径

后续若要修改规则文件，建议只加一条轻量原则：

> Spec 是行为意图的临时或持久事实源：常规风险用最小 delta 解释行为变化，高风险用 `docs/approved/` 承担审批基线；任何进入 Story 路径的 spec 必须能追到 AC 与测试。

这条口径兼容 OpenSpec 的 delta/archive，也兼容 Spec Kit 的 specify/plan/tasks 分层，同时不引入新的强制工具依赖。

## 不采用清单

- 不新增 `openspec/` 或 `.specify/` 作为全仓库默认结构。
- 不要求所有 PR 附 spec 文档。
- 不要求 agent 在低风险任务中先写 spec。
- 不把第三方 CLI 纳入 `cloud-agent-bootstrap.sh` 默认安装。
- 不把“计划已生成”当成验证；验证仍以可执行测试、运行输出和人工可 review 的 evidence 为准。

