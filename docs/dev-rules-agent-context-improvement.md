# dev-rules 进阶改进方案：Harness、记忆结构与 OPC 专家团

> 状态：Phase 1 已获批准并部分落地；本文仍作为进阶设计依据，不替代执行规则。  
> 核心依据：`digital-clone-research.md` 的 Jobs + OPC 哲学、PR #6 的 OpenSpec / Spec Kit 调研、`qoder_shared_by_alibaba_from_images.md` 的 Qoder OCR 输入，以及 Anthropic / Claude Code / Spec Kit 官方公开材料。  
> 一句话结论：`dev-rules` 不应演化为大而全 Agent 平台，而应成为 **OPC Agent Operating System** 的规则内核：用最少制度资产约束 Agent 的上下文、动作、记忆、专家协作与验证闭环。

---

## 0. 设计边界

本文讨论的是 `dev-rules` 的下一阶段能力设计，不是立即实施清单。

`dev-rules` 的职责边界应保持清晰：

```text
Agent Constitution + Harness Guardrails + Minimal Workflow + Expert Routing
```

它应该管：

- Agent 读什么上下文
- 什么时候需要意图载体
- 什么时候需要专家团
- 哪些动作必须被权限 / hook / preflight 约束
- 哪些经验应沉淀为 memory，哪些应升级为规则或脚本
- review / test / approval 如何形成闭环

它不应该管：

- 第三方平台私有实现细节
- 全功能 Agent orchestration 平台
- 默认强制所有任务走 SDD
- 模型供应商绑定
- 大而全知识库系统

这是 Jobs 的聚焦，也是 OPC 的流程极简。

---

## 1. 外部输入提炼

## 1.1 Qoder：Coding Agent 的上下文体系

`qoder_shared_by_alibaba_from_images.md` 中最重要的一页是“Coding Agent - 上下文体系”：

- 短期记忆：System / User / Assistant / Tool Message、Latest User Message
- 长期记忆：历史会话、代码仓库架构信息、开发者偏好、项目规则、经验教训

对 `dev-rules` 的启发：

> Agent 的稳定性不是只靠 prompt，而是靠上下文分层、加载顺序和事实源优先级。

当前 `dev-rules` 已有 `global/CLAUDE.md`、`rules/*.mdc`、commands、memory、docs，但还缺一个显式口径：**什么信息属于规则、什么属于记忆、什么属于当前变更证据、什么必须被脚本机械验证。**

## 1.2 Qoder：企业落地 AI Coding 的挑战

OCR 中“企业落地 AI Coding 的二大挑战与基础保障”强调：

- 效果挑战：复杂工程理解不足、代码幻觉、技术栈适配困难
- 知识管理挑战：私域知识注入、代码规范统一、最佳实践沉淀
- 底座保障：安全合规、团队接受度、落地策略、成本与 ROI

对 `dev-rules` 的启发：

> 真正的瓶颈不是 Agent 会不会写代码，而是它是否读到了正确上下文、是否按正确边界行动、是否能把经验升级为制度资产。

## 1.3 Qoder：Harness Agent 配置要素

OCR 中 Harness Agent 生成配置要素包括：

1. 上下文与记忆
2. 工具集与技能
3. 模型集成与路由
4. 运行时引擎
5. 安全围栏与权限
6. 编排与多智能体

对 `dev-rules` 的取舍：

- 应吸收：上下文压缩与注入、动态工具权限、感知-推理-动作循环、安全围栏、监督与移交、任务分解与智能体编排
- 不照搬：平台级模型调度、企业控制台、复杂用量 API、完整多租户治理

## 1.4 Qoder：专家团

OCR 中专家团形态是：

- Experts Leader 统一协调
- SWE Agent 各司其职
- 共享文件存储
- 并发执行、派发任务、汇报结果、异步通信
- 角色包括：调研专家、前端编码专家、后端编码专家、测试专家、前端测试专家、代码评审专家

对 `dev-rules` 的启发：

> 专家团不是“多开几个 Agent”，而是以明确角色、任务边界、共享证据和 Lead synthesis 构成的协作机制。

但按照 Jobs / OPC，专家团不能成为默认流程，只能用于高价值、可并行、边界清晰的任务。

## 1.5 Anthropic / Claude Code 官方材料校准

公开材料给出几个关键校准点：

- 多 Agent 适合高价值、可并行、广度探索任务；不适合强耦合、顺序依赖、同文件频繁编辑任务。
- Lead / worker 模式要明确：Lead 负责拆解、派发、合成、决策；worker 负责独立探索或执行。
- 子 Agent / teammates 应隔离上下文，只回传高信号摘要。
- 专家团有显著 token 与协调成本，必须有触发门槛和停止规则。
- Hooks / settings / permissions 是 deterministic harness，适合放在随机 Agent 外层做安全和流程约束。

这与 `digital-clone-research.md` 的核心原则完全一致：

```text
人类只介入判断；Agent 承担执行；规则、记忆、技能代码化、版本化；反复失守的软规则升级为机械检查。
```

---

## 2. 总体设计：OPC Agent Operating System

`dev-rules` 的进阶形态不应是“文档仓库”，而应是一个最小 Agent OS 内核。

```text
                   Human Owner
                 高风险审批 / 架构判断
                         │
                         ▼
             dev-rules Constitution
       Jobs 聚焦 / OPC 杠杆 / 风险分级 / 不做什么
                         │
                         ▼
┌────────────────────────────────────────────────────┐
│                    Harness Layer                    │
│  context → tools → permissions → hooks → preflight   │
└────────────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────┐
│                    Memory Layer                     │
│  rules / memory / specs / evidence / metrics         │
└────────────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────┐
│                  Expert Team Layer                  │
│  lead → specialists → synthesis → validation         │
└────────────────────────────────────────────────────┘
                         │
                         ▼
                 Code / Tests / Review / PR
```

设计目标不是“能力最多”，而是：

- 默认路径更轻
- 高风险路径更稳
- 上下文更准
- 专家团更可控
- 经验更容易沉淀为制度资产

---

## 3. Harness 设计

## 3.1 Harness 的定义

在 `dev-rules` 中，Harness 不是一个新平台，而是一组围绕 Agent 的确定性约束：

```text
任务输入
→ 上下文选择
→ 风险判定
→ 工具/权限边界
→ 执行动作
→ 验证证据
→ review / preflight
→ 经验沉淀
```

它的价值是把随机 Agent 包在确定性的工作流里。

## 3.2 Harness 六件套

借鉴 Qoder Harness 页，但压缩成 `dev-rules` 可维护的六件套：

| Harness 要素 | dev-rules 形态 | 目标 |
| --- | --- | --- |
| Context | `CLAUDE.md`、`rules/*.mdc`、docs、memory 读取顺序 | 防止上下文错配 |
| Tools | commands、skills、MCP、Bash/Read/Edit 权限 | 防止工具失控 |
| Permissions | Claude Code settings / permission rules | 防止危险动作 |
| Hooks | PreToolUse / PostToolUse / Stop 等事件 | 把软规则外置成机械门禁 |
| Runtime | 默认单 Agent，必要时 subagent / expert team | 控制协调成本 |
| Evidence | tests、preflight、review JSON、spec delta | 防止“看起来完成” |

## 3.3 Harness 的最小闭环

建议把默认任务闭环定义为：

```text
Intent → Risk → Context → Action → Evidence → Review → Memory/Rule Promotion
```

### Intent

明确用户意图和不做什么。

### Risk

按 `rules/product-dev.mdc` 判定低风险 / 常规风险 / 高风险。

### Context

按上下文优先级读取，不把 memory 当事实源。

### Action

按工具权限执行，危险动作先确认。

### Evidence

用测试、运行输出、preflight、review 证明，而不是自然语言声称。

### Review

先审 intent，再审 code，最后审 validation。

### Memory / Rule Promotion

一次性经验进 memory；反复失守升级为 rule / schema / preflight。

## 3.4 Harness Hook 设计方向

Claude Code hooks 适合做 deterministic guardrails。建议后续按以下优先级设计，而不是一开始铺满：

### H1. 危险动作拦截

适合 `PreToolUse`：

- destructive git
- 删除文件
- 生产环境命令
- secrets 读取
- 未授权 push / PR / issue 操作

原则：**拦截副作用前的动作，不在事后补救。**

### H2. 文件编辑后提醒验证

适合 `PostToolUse`：

- 修改规则文件后提醒 `sync.sh --local` / `verify-rules.sh`
- 修改代码后提示相关 test / preflight
- 修改 docs 中 stat 后提示 `sync-stats.sh --check`

原则：先提醒，等反复失守再升级为阻断。

### H3. Stop 前自检

适合 `Stop`：

- 若存在未完成任务，提示不要汇报完成
- 若修改过文件但未验证，提示明确“未验证”
- 若高风险路径未审批，阻止继续声称完成

原则：防止 Agent 在没有 evidence 时输出“完成”。

## 3.5 Harness 不做什么

- 不默认启用复杂 hook 网络
- 不让 hooks 调用 LLM 做判断
- 不把所有提醒变成阻断
- 不做平台级 telemetry 控制台
- 不用 hook 替代 preflight

Jobs 原则：机制越少越好。OPC 原则：反复出错的机制才硬化。

---

## 4. 记忆结构设计

## 4.1 当前问题

Qoder 的“记忆”产品形态提醒了一个关键点：长期记忆不是一个桶，而是不同类型的制度资产。

如果 memory 混入：

- 用户偏好
- 项目规则
- 架构事实
- 错误修复总结
- 当前任务状态
- 外部参考

Agent 很容易把过期记忆当事实，把一次性经验当规则，把当前任务状态保存成长期负担。

## 4.2 dev-rules 记忆分层

建议明确五层：

| 层级 | 名称 | 载体 | 是否事实源 | 生命周期 |
| --- | --- | --- | --- | --- |
| L0 | Current Context | 当前 user prompt、当前代码、当前 diff | 是 | 当前任务 |
| L1 | Constitution / Rules | `global/CLAUDE.md`、`rules/*.mdc` | 是 | 长期 |
| L2 | Approved Specs | `docs/approved/`、Story/Test、spec delta | 条件事实源 | 随 PR / 项目演进 |
| L3 | Memory | user / feedback / project / reference memory | 否，需验证 | 中长期 |
| L4 | Lessons / Metrics | review 校准、失败模式、ROI 指标 | 否，供升级判断 | 周期性清理 |

核心原则：

> Memory 是检索线索，不是当前真相。当前真相必须回到代码、规则、审批产物、测试证据。

## 4.3 记忆写入规则

### 应写入 memory

- 用户长期偏好
- 反复出现的协作偏好
- 项目背景和外部系统入口
- 非代码可推导的业务约束

### 不应写入 memory

- 当前任务进度
- git diff 摘要
- 代码结构和文件路径快照
- 一次性修复 recipe
- 已在规则中明确的内容

### 应升级为 rule / preflight

- review 多次发现的同类问题
- 高风险审批反复遗漏项
- 文档漂移反复发生处
- 测试证据缺失反复出现处

## 4.4 Context Pack：高信号上下文包

建议后续引入一个轻量概念：**Context Pack**。

它不是新目录，也不是全文知识库，而是任务开始时 Agent 应优先加载的一组高信号引用：

```text
- 当前任务输入
- 相关 rules
- 相关 approved/spec/story
- 相关 memory pointer
- 相关代码入口
- 本次验证命令
```

Context Pack 可以先不落盘，只作为 `/user:decompose` 或专家团 Lead 的输出小节。

目标是：

- 防止 Agent 漫无目的读文件
- 防止上下文塞太满
- 防止专家团成员读到不同事实源

## 4.5 记忆压缩与归档

长期应建立一个简单原则：

```text
memory 越旧，越要从“事实陈述”退化为“查找线索”。
```

例如：

- “某文件存在某函数”不应长期保存
- “这个项目的计费规则由某审批文档决定”可以保存为 reference
- “用户偏好 review 只报阻塞问题”可以保存为 feedback

---

## 5. 专家团设计

## 5.1 专家团的定位

基于 `dev-rules` 打造专家团，目标不是模拟大公司多人协作，而是服务 OPC：

```text
一个人 + N 个专业 Agent = 小团队产出，但没有组织负担
```

专家团应只在满足以下条件时启用：

- 任务高价值
- 可并行拆分
- 子任务边界清晰
- 各专家可以独立产出 evidence
- Lead 能合成结论

不应启用专家团的场景：

- 单文件小修改
- 顺序依赖强
- 多个 Agent 会编辑同一文件
- 需求本身不清楚且没有先澄清
- token 成本高于潜在收益

## 5.2 专家团基本形态

```text
Human Owner
   │ 高风险审批 / 架构判断
   ▼
Expert Lead
   │ 拆解、派发、去重、合成、决策建议
   ├── Research Expert
   ├── Product / Spec Expert
   ├── Architecture Expert
   ├── Frontend Expert
   ├── Backend Expert
   ├── Test Expert
   ├── Security Expert
   └── Review Expert
```

专家团必须有 Lead。没有 Lead 的并发 Agent 只是噪音放大器。

## 5.3 专家角色定义

### Expert Lead

职责：

- 判断是否值得启用专家团
- 生成 Context Pack
- 拆解任务和依赖
- 分配角色
- 控制专家数量
- 合成结论
- 标记未解决冲突
- 向人类提出需要审批的判断点

不做：

- 不把所有任务都派发
- 不让专家团绕过高风险审批
- 不把专家意见直接当事实

### Research Expert

适用：外部调研、竞品调研、方案比较、未知库/API 调查。

产出：

- 关键发现
- 来源链接或文件路径
- 可采纳 / 不采纳建议
- 不确定性

### Product / Spec Expert

适用：需求澄清、spec delta、用户旅程、验收标准。

产出：

- 核心场景
- 不做什么
- ADDED / MODIFIED / REMOVED
- 待澄清项

### Architecture Expert

适用：高风险架构边界、公共契约、状态机、数据模型。

产出：

- 架构影响
- 可逆性
- 迁移风险
- 是否需要 `docs/approved/`

### Frontend Expert

适用：UI / UX / WebUI / 交互验证。

产出：

- 用户路径
- 视觉/交互风险
- 浏览器验证点
- 前端测试建议

### Backend Expert

适用：API、业务逻辑、数据流、后台任务。

产出：

- 行为边界
- 错误处理
- 数据一致性风险
- 相关测试点

### Test Expert

适用：测试设计、Story/Test 对齐、验证证据。

产出：

- 核心正向 / 负向 / 回归测试
- 是否需要 Story 路径
- 验证命令
- 缺失 evidence

### Security Expert

适用：鉴权、授权、输入安全、secrets、租户隔离、供应链风险。

产出：

- 安全边界
- 风险等级
- 必须阻断项
- 最小修复建议

### Review Expert

适用：PR 审查、符合性检查、最终合成前的冷读。

产出：

- critical/high findings
- validation gaps
- 与 intent/spec/approval 的偏离
- 是否 merge-ready

## 5.4 专家团触发规则

建议后续写入 `/user:decompose` 或新命令口径：

```text
默认不用专家团。
只有当任务满足“高价值 + 可并行 + 边界清晰 + 需要多视角”时，才建议启用。
```

### 强触发候选

- 高风险变更，且影响架构 / 安全 / 数据 / 公共契约
- 大型 PR review，需要安全、测试、架构并行审查
- 根因不明的复杂 bug，需要竞争假设
- 跨前后端和测试的功能，但文件边界可拆
- 需要外部调研 + 本地实现方案结合

### 弱触发候选

- 常规风险但 reviewer 成本明显较高
- 需要快速比较多个技术方案
- 用户明确要求“专家团”或“多角色审查”

### 禁止触发

- 低风险文档/命名/格式化
- 单点 bug fix
- 同一文件集中修改
- 需求未澄清

## 5.5 专家团工作流

```text
1. Lead 判断是否启用专家团
2. Lead 输出 Context Pack
3. Lead 拆解任务与角色
4. 专家并行工作，各自产出短报告
5. Lead 合成：共识 / 冲突 / 风险 / 建议
6. 若涉及高风险判断，暂停给人类审批
7. 进入实现或 review
8. 将重复经验沉淀为 memory / rule / preflight
```

## 5.6 专家报告格式

为了防止专家输出散文，建议统一短格式：

```markdown
## Role
[专家角色]

## Scope
[本次只看什么]

## Findings
- [结论 + 证据路径/来源]

## Risks
- [真实风险；没有则写 none]

## Recommendation
- [一条最小建议]

## Confidence
High / Medium / Low，说明不确定性
```

## 5.7 专家团的硬约束

- 每个专家必须有独立 scope
- 每个专家必须输出 evidence
- Lead 必须合成，不得拼贴
- 同一文件不得由多个实现专家并行编辑
- 高风险决策必须回到人类审批
- 专家数量默认少于任务数量，避免管理成本反噬
- 研究 / review 优先使用专家团，实现谨慎使用专家团

## 5.8 专家团与 Claude Code 能力映射

| 场景 | 首选机制 | 原因 |
| --- | --- | --- |
| 单次聚焦调查 | Subagent | 上下文隔离，成本较低 |
| 多视角 review | Subagents 或 Agent Team | 可并行，结果由 Lead 合成 |
| 需要专家互相讨论 | Agent Team | 支持 teammate 通信与共享任务 |
| 多文件独立实现 | Agent Team / worktree | 需避免文件冲突 |
| 顺序强依赖实现 | 单 Agent | 协调成本低 |
| 高风险方案设计 | Plan + 专家审查 + 人类审批 | 判断权不交给 Agent |

---

## 6. Spec Delta 与专家团的关系

PR #6 的 OpenSpec / Spec Kit 调研仍然成立，但应被放入更大的 Harness 设计中。

### Spec Delta 是意图载体

适用于常规风险行为变更：

```text
docs/spec-delta-<slug>.md
```

只保留：

- Background
- ADDED / MODIFIED / REMOVED
- Scenarios
- Validation

### Spec / Plan / Tasks 分层

借鉴 Spec Kit，但压缩为 `dev-rules` 口径：

| 层 | 回答 | 禁止 |
| --- | --- | --- |
| Spec | what / why | 偷跑技术方案 |
| Plan | how | 替代高风险审批 |
| Tasks | who / order / validation | 重新定义需求 |

### 专家团如何使用 Spec Delta

- Product / Spec Expert 起草或审查 spec delta
- Architecture Expert 判断是否升级 `docs/approved/`
- Test Expert 把 scenario 转成测试建议
- Review Expert 检查 code 是否符合 spec delta
- Lead 负责合成，不让 spec delta 膨胀成设计长文

---

## 7. 度量与 ROI

Qoder OCR 中强调“度量是指标，不是提升手段”。这点对 `dev-rules` 非常关键。

不建议追求：

- AI 生成代码占比
- 编辑次数
- Agent 调用次数
- 文档数量
- 专家数量

这些容易变成 vanity metrics。

建议关注：

| 指标 | 含义 | 用途 |
| --- | --- | --- |
| Review 阻塞问题重复率 | 同类问题是否反复出现 | 判断是否升级 preflight |
| Validation gap 率 | PR 是否缺验证证据 | 判断测试纪律是否生效 |
| 高风险误判率 | 是否过度/不足升级 | 校准风险分级 |
| 返工原因 | 是需求误解、实现错误还是验证不足 | 改进 harness |
| 专家团命中率 | 专家团是否产出非显而易见价值 | 决定是否保留该模式 |

原则：

> 指标只用于发现下一条该机械化的规则，不用于制造管理仪表盘。

---

## 8. 安全与权限设计

Qoder OCR 多次提到安全合规、隐私模式、密钥管理、最小权限。`dev-rules` 的安全策略应保持工程化：

## 8.1 权限分层

- 项目级 settings：团队共享的安全默认值
- 本地 settings：个人偏好与本机工具
- managed settings：组织不可绕过策略

## 8.2 高价值拦截点

优先机械化：

- secrets 文件读取
- destructive shell
- git push / force push
- 生产数据库或云资源命令
- 未批准的高风险路径推进

## 8.3 不做

- 不把所有命令默认封死
- 不用 LLM 判断安全策略
- 不让权限规则变成开发阻碍

---

## 9. 建议落地路线

## Phase 1：文档与规则口径收敛

目标：先建立统一语言，不增加默认流程成本。

实现进展（2026-04-28）：

- `rules/product-dev.mdc` 增加按风险匹配意图载体的规则：低风险靠任务说明与测试，常规风险按需使用 spec delta，高风险进入 `docs/approved/`。
- `commands/decompose.md` 增加 Context Pack、spec / plan / tasks 边界、专家团触发与禁用条件。
- `commands/review.md` 增加 `Intent → Code → Validation` 审查顺序，并将 spec delta 定位为常规风险意图载体而非审批基线。
- `rules/test-philosophy.mdc` 增加 spec / scenario 到测试、手动验证或明确未验证说明的链路。
- `README.md` 增加本文入口，说明本文是设计依据，不替代执行规则。

同期修复：

- 新项目接入与本机首次安装示例改为 `DEV_RULES_REMOTE_URL` 必填环境变量，无默认值，避免把 dev-rules 远端 URL 写入不希望暴露的消费仓库。
- fan-out 语义从“必须同时在 `.registered-projects` 与 `.local-projects` 中存在”收敛为“本机存在 materialized path 即参与”，支持 `.local-projects` local-only 项目。
- `sync.sh --list` / `--status` 显示 materialized fan-out targets，避免展示语义落后于实际同步语义。

未落地：Phase 2 专家报告协议、Phase 3 hooks、Phase 4 指标校准仍保持候选方向，等出现重复失守或明确高价值场景再硬化。

## Phase 2：轻量专家团协议

目标：定义专家团何时用、怎么用、怎么输出。

候选改动：

- 新增专家角色口径到命令或规则
- 专家报告统一短格式
- 明确默认不用专家团
- 明确专家团优先用于 research / review / competing hypotheses

## Phase 3：Harness hooks 最小化

目标：只把反复失守的问题机械化。

候选方向：

- 危险命令 `PreToolUse` 阻断
- 修改规则后提醒验证
- Stop 前检查未验证状态
- 高风险未审批防误报完成

## Phase 4：校准与升级

目标：用 review / validation 反馈决定是否继续硬化。

候选方向：

- 汇总 review JSON 的 repeated findings
- 统计 validation gaps
- 记录专家团是否产生独立价值
- 只把高频、真实、可机械化的问题升级为脚本

---

## 10. 明确不做

### 不做完整平台

`dev-rules` 不做 Qoder / Claude Code / Cursor 的替代平台。

### 不做默认专家团

专家团是高价值任务工具，不是每个任务的仪式。

### 不做全文知识库

不把所有文档、聊天、代码都塞进上下文。只加载最小高信号上下文。

### 不做指标崇拜

不追 AI 代码占比、调用次数、专家数量。只看返工、阻塞问题、验证缺口是否下降。

### 不做模型绑定

模型选择可以有原则，但不写死供应商或具体模型到规则内核。

### 不做不可验证规则

不能被 review、test、preflight、approval 或 memory 检索闭环使用的规则，不进入 `dev-rules`。

---

## 11. 建议新增总原则

后续可将以下原则拆入规则文件：

> Agent 工作必须由 Harness 包裹：先用最小上下文理解意图，再按风险选择意图载体与专家协作方式，执行过程受工具权限与安全围栏约束，完成状态必须由测试、preflight、review 或审批证据支撑；一次性经验进入 memory，反复失守的问题升级为规则、schema 或脚本。

专家团原则：

> 专家团只服务高价值、可并行、边界清晰的任务；Lead 负责拆解与合成，专家只在自己的 scope 内产出带证据的短报告。高风险判断始终回到人类审批。

记忆原则：

> Memory 是上下文线索，不是事实源；当前事实以代码、规则、审批产物和测试证据为准。重复经验必须从 memory 晋升为可执行制度资产。

---

## 12. 审批建议

建议优先审批：

1. Harness 最小闭环口径
2. 记忆分层与 Context Pack 口径
3. 专家团触发规则与角色协议
4. Spec Delta 与 Intent → Code → Validation review 顺序

建议暂缓：

- hook 大规模落地
- agent team 默认启用
- 指标系统建设
- 新目录体系或复杂模板库

原因：先统一语言和边界，观察真实任务中的失败模式，再把反复失守的问题机械化。

---

## 13. Sources

- Qoder OCR 输入：`qoder_shared_by_alibaba_from_images.md`
- `digital-clone-research.md`
- `docs/spec-methods-openspec-speckit.md`
- Anthropic: [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- Anthropic: [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Claude Code Docs: [Create custom subagents](https://code.claude.com/docs/en/sub-agents)
- Claude Code Docs: [Hooks](https://code.claude.com/docs/en/hooks)
- Claude Code Docs: [Agent teams](https://code.claude.com/docs/en/agent-teams)
- Claude Code Docs: [Settings](https://code.claude.com/docs/en/settings)
- Spec Kit: [Official site](https://speckit.org/)
- Microsoft for Developers: [Diving Into Spec-Driven Development With GitHub Spec Kit](https://developer.microsoft.com/blog/spec-driven-development-spec-kit)
