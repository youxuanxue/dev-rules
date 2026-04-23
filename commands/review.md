对当前项目进行代码审查：

$ARGUMENTS

如果未指定审查范围，则审查最近 24 小时内的所有 commit。

## 第一步：先判断这次变更的风险等级

风险分级直接遵循 `rules/product-dev.mdc`，不要在本命令里另写一套判定标准。输出时使用以下标签：

- `low`
- `normal`
- `high`

将风险等级写入输出中的 `risk_level` 字段。

## 第二步：按风险决定审查深度

### 低风险 / 常规风险

默认做**精简审查**：

- 优先发现阻塞问题、真实风险、缺失验证
- 默认不输出大而全的审计散文
- 默认只输出值得打断作者的 `critical` / `high` 问题
- `medium` / `low` 只有在确实值得立即修时才输出

### 高风险

做**展开审查**：

- 除通用质量检查外，增加对 `docs/approved/`、`.testing/user-stories/`、`CLAUDE.md` 的符合性检查
- 输出完整证据链，包括 `approved_artifacts_referenced` 与 `conformance_summary`
- 当存在符合性偏离、迁移风险、安全边界问题时，明确列为阻塞项

将审查模式写入输出中的 `review_mode` 字段：

- `concise`
- `full_conformance`

## 审查前置：按需加载审批产物

只有高风险审查或变更显式触达这些基线时，才读取以下目录作为符合性检查基线：

1. `docs/approved/` — 人类审批通过的设计文档、API 契约、数据模型、技术选型
2. `.testing/user-stories/` — User Story 及其验收标准（AC）
3. `CLAUDE.md` — 项目架构约束

加载 `docs/approved/` 时，检查每个文件的 YAML frontmatter 元数据头：

- `approved_by: pending` → 该文件尚未通过人工审批，在报告中标注"未审批产物：{文件名}"，不作为符合性基线
- 缺少元数据头 → 在报告中标注"缺少审批元数据：{文件名}"，仍作为基线但降低置信度
- `approved_by` 为具体人名 → 正常作为符合性基线

如果高风险审查需要符合性检查，但 `docs/approved/` 不存在或为空，在报告中注明"无法执行完整符合性检查：缺少审批产物"。

## 审查维度

### 维度一：通用代码质量（所有风险等级都做）

#### 1. 代码质量

- 是否存在明显的逻辑错误或边界问题
- 是否有未处理的错误/异常
- 命名是否清晰、一致
- 是否有不必要的复杂度

#### 2. 安全性

- 是否有硬编码的密钥、Token、密码
- 是否有 SQL 注入、XSS、路径穿越等风险
- 是否遵循最小权限原则
- 输入验证是否充分

#### 3. 测试覆盖

- 新增功能是否有对应测试
- 测试是否包含正向和负向场景
- 是否存在"存在性测试"（仅断言文件存在，而非验证行为）

#### 4. 架构一致性

- 是否遵循分层依赖（entry → command → domain → shared）
- 是否有反向依赖或循环引用
- 如果触达公共契约，是否同步更新了契约文档

#### 5. 原型 vs 生产代码检查

- 仅当存在高风险原型路径时检查：
  - 原型代码（`prototype/` 分支）是否被错误合入生产分支
  - 原型阶段的临时 hack 是否在功能实现阶段被清理

#### 6. 可维护性

- 是否有足够的（非冗余的）注释解释非显而易见的逻辑
- 是否有 TODO/FIXME/HACK 需要跟踪
- 变更是否会影响其他模块

#### 7. 设计质量

按 `digital-clone-research.md §一` 的 Jobs/OPC 哲学落到具体审查项：

- 不必要的抽象层 / 配置项 / 参数（Jobs 简洁）
- 公共接口/导出符号没有真实调用方（Jobs 最小 API 面）
- 实现超出任务边界，把 `docs/task-breakdown-*.md` 「聚焦决策」里声明"不做"的功能偷偷加回（Jobs 聚焦）
- 引入需要人手执行的步骤（手动迁移/配置/重启）而未脚本化（OPC 自动化）
- 新增无法证明价值的流程节点（OPC 流程极简）

category 标记为 `design-quality`；severity 默认 medium，引入大量手动操作或显著膨胀时 high。

### 维度二：符合性检查（仅高风险或显式要求时）

对照 `docs/approved/` 中人类审批通过的产物，逐项检查代码实现是否一致：

#### 8. 代码 ↔ 设计文档

- 数据模型的字段、类型、约束是否与审批的模型一致
- 业务流程/状态机是否与审批的流程图一致
- 如有偏离，标注具体偏离项和审批产物中的对应章节

#### 9. 代码 ↔ API 契约

- 接口路径、HTTP 方法是否与审批的 OpenAPI/接口文档一致
- 请求参数（名称、类型、必填性）是否与契约一致
- 响应结构和状态码是否与契约一致
- 如有偏离，引用契约文件的具体路径

#### 10. 代码 ↔ 验收标准

- User Story 的 AC（Given/When/Then）是否被代码行为覆盖
- 负向 AC（错误场景）是否有对应的校验和错误返回
- 引用 `.testing/user-stories/` 中的具体 Story ID 和 AC 编号

#### 11. 代码 ↔ 技术选型

- 是否使用了审批确认的技术栈、依赖、框架
- 是否引入了未经审批的新依赖或技术方案

#### 12. 代码 ↔ 任务边界

- 实现是否超出了分配的任务范围（scope creep）
- 是否遗漏了任务要求的功能点

每个 conformance finding 必须包含 `reference` 字段，指向审批产物的文件路径和具体章节/行号。

## 审查流程

1. 先判定 `risk_level` 与 `review_mode`
2. 按需读取审批产物（`docs/approved/`、`.testing/user-stories/`、`CLAUDE.md`）
3. 获取审查范围内的所有变更：
  ```bash
   git log --oneline --since="24 hours ago"
   git diff HEAD~N
  ```
4. 逐文件分析变更：先做通用质量检查；只有在高风险或显式要求时再做符合性检查
5. 按严重程度分级（JSON `severity` 字段取值）：
  - **critical**：阻塞合并（安全漏洞、数据丢失风险）
  - **high**：必须修复（与审批产物严重偏离、架构问题）
  - **medium**：建议修复（代码质量、缺少测试、与审批产物轻微偏离）
  - **low**：改进建议（风格、命名、可读性）
6. 默认只输出值得打断作者的发现：
  - `critical` / `high`：默认输出
  - `medium` / `low`：仅在明确值得立即处理时输出
7. Markdown 摘要中的映射：critical/high → CRITICAL，medium → WARNING，low → INFO

## 输出格式

将报告以 **JSON** 格式输出到 `docs/review-$(date +%Y%m%d).json`。默认输出保持精简；只有 `review_mode=full_conformance` 时，才要求完整符合性字段。

**强约束**：JSON 必须能通过 `dev-rules/schemas/review.schema.json`（JSON Schema Draft 2020-12）的校验。`/user:calibrate` 在汇总指标前会拒绝任何不合规文件。校验涵盖：必填字段、severity/category 枚举、`conformance` 类必须含 `reference`、`human_verdict` 结构，以及高风险模式下的额外字段。

每个 finding 必须包含一个空的 `human_verdict` 对象，供人工校准时填写：

```json
{
  "review_date": "2026-04-21",
  "scope": "HEAD~5..HEAD",
  "risk_level": "high",
  "review_mode": "full_conformance",
  "decision": "needs-fix",
  "approved_artifacts_referenced": [
    "docs/approved/data-model.md"
  ],
  "validation_gaps": [
    "未见并发场景测试"
  ],
  "summary": {
    "critical": 1,
    "high": 0,
    "medium": 0,
    "low": 0
  },
  "conformance_summary": {
    "total_findings": 1,
    "artifacts_checked": [
      "docs/approved/data-model.md"
    ],
    "by_artifact": {
      "docs/approved/data-model.md": 1
    }
  },
  "findings": [
    {
      "severity": "critical",
      "category": "conformance",
      "automatable": false,
      "file": "src/models.py",
      "line": 30,
      "description": "...",
      "reference": "docs/approved/data-model.md#审批步骤表",
      "suggested_fix": "...",
      "human_verdict": {}
    }
  ]
}
```

人工校准时，reviewers 在 `human_verdict` 中填写：

```json
"human_verdict": {
  "accurate": true,
  "severity_correct": true,
  "autofix_safe": true,
  "notes": "可选备注"
}
```

- `accurate`：该发现是否确实是问题（false = 误报）
- `severity_correct`：严重程度分级是否合理
- `autofix_safe`：如果 `automatable=true` 且已自动修复，修复是否安全（未引入新问题）
- `notes`：可选的人工备注

标注完成后运行 `/user:calibrate` 命令自动汇总校准指标。

同时生成一份人类可读的 Markdown 摘要到 `docs/review-$(date +%Y%m%d).md`。默认摘要保持短，只保留必须决策的信息：

```markdown
# 代码审查报告

> 审查日期：[日期]
> 审查范围：[commit 范围]
> 风险等级：[low / normal / high]
> 审查模式：[concise / full_conformance]
> 审批产物：[已加载的审批文件列表，或"未加载 / 未找到"]

## 摘要
- 结论：merge-ready / needs-fix / needs-design-review
- CRITICAL/HIGH: X 项
- Validation gaps: [若无则写 none]

## 关键发现
### [R-001] [文件路径:行号] [问题标题]
- 严重程度：critical / high
- 问题描述：...
- 建议修复：...

## 符合性偏离（仅高风险模式或显式要求时输出）
### [CF-001] [文件路径] 与 [审批产物路径#章节] 不一致
- 审批产物要求：...
- 实际实现：...
- 建议修复：...

## 可选建议（仅在确实值得作者立即处理时输出）
- ...
```

默认只要求 JSON 输出；Markdown 摘要只在以下情况生成：

- `review_mode=full_conformance`
- 用户明确要求人类可读摘要
- 需要把审查结果直接贴进 PR comment / review note

如果 docs 目录不存在则创建。