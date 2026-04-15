对当前项目进行代码审查：

$ARGUMENTS

如果未指定审查范围，则审查最近 24 小时内的所有 commit。

## 审查前置：加载审批产物

在开始审查前，读取以下目录中的文件作为符合性检查的基线：

1. `docs/approved/` — 人类审批通过的设计文档、API 契约、数据模型、技术选型
2. `.testing/user-stories/` — User Story 及其验收标准（AC）
3. `CLAUDE.md` — 项目架构约束

加载 `docs/approved/` 时，检查每个文件的 YAML frontmatter 元数据头：
- `approved_by: pending` → 该文件尚未通过人工审批，在报告中标注"未审批产物：{文件名}"，不作为符合性基线
- 缺少元数据头 → 在报告中标注"缺少审批元数据：{文件名}"，仍作为基线但降低置信度
- `approved_by` 为具体人名 → 正常作为符合性基线

如果 `docs/approved/` 不存在或为空，在报告中注明"无法执行符合性检查：缺少审批产物"。

## 审查维度

### 维度一：通用代码质量

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
- API 变更是否同步更新了契约文档

#### 5. 原型 vs 生产代码检查

- 原型代码（`prototype/` 分支）是否被错误合入生产分支
- 原型阶段的临时 hack 是否在功能实现阶段被清理

#### 6. 可维护性

- 是否有足够的（非冗余的）注释解释非显而易见的逻辑
- 是否有 TODO/FIXME/HACK 需要跟踪
- 变更是否会影响其他模块

### 维度二：符合性检查（对照审批产物）

对照 `docs/approved/` 中人类审批通过的产物，逐项检查代码实现是否一致：

#### 7. 代码 ↔ 设计文档

- 数据模型的字段、类型、约束是否与审批的模型一致
- 业务流程/状态机是否与审批的流程图一致
- 如有偏离，标注具体偏离项和审批产物中的对应章节

#### 8. 代码 ↔ API 契约

- 接口路径、HTTP 方法是否与审批的 OpenAPI/接口文档一致
- 请求参数（名称、类型、必填性）是否与契约一致
- 响应结构和状态码是否与契约一致
- 如有偏离，引用契约文件的具体路径

#### 9. 代码 ↔ 验收标准

- User Story 的 AC（Given/When/Then）是否被代码行为覆盖
- 负向 AC（错误场景）是否有对应的校验和错误返回
- 引用 `.testing/user-stories/` 中的具体 Story ID 和 AC 编号

#### 10. 代码 ↔ 技术选型

- 是否使用了审批确认的技术栈、依赖、框架
- 是否引入了未经审批的新依赖或技术方案

#### 11. 代码 ↔ 任务边界

- 实现是否超出了分配的任务范围（scope creep）
- 是否遗漏了任务要求的功能点

每个 conformance finding 必须包含 reference 字段，指向审批产物的文件路径和具体章节/行号。

## 审查流程

1. 读取审批产物（`docs/approved/`、`.testing/user-stories/`、`CLAUDE.md`）
2. 获取审查范围内的所有变更：
  ```bash
   git log --oneline --since="24 hours ago"
   git diff HEAD~N
  ```
3. 逐文件分析变更：先做通用质量检查，再做符合性检查
4. 按严重程度分级（JSON severity 字段取值）：
  - **critical**：阻塞合并（安全漏洞、数据丢失风险）
  - **high**：必须修复（与审批产物严重偏离、架构问题）
  - **medium**：建议修复（代码质量、缺少测试、与审批产物轻微偏离）
  - **low**：改进建议（风格、命名、可读性）

  Markdown 摘要中的映射：critical/high → CRITICAL，medium → WARNING，low → INFO

## 输出格式

将报告以 **JSON** 格式输出到 `docs/review-$(date +%Y%m%d).json`，遵循结构化审查格式（含 `approved_artifacts_referenced`、`conformance_summary`、每个 finding 的 `category` 和 `reference` 字段）。

每个 finding 必须包含一个空的 `human_verdict` 对象，供人工校准时填写：

```json
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

同时生成一份人类可读的 Markdown 摘要到 `docs/review-$(date +%Y%m%d).md`：

```markdown
# 代码审查报告

> 审查日期：[日期]
> 审查范围：[commit 范围]
> 审批产物：[已加载的审批文件列表，或"未找到"]

## 摘要
- CRITICAL: X 项（含 N 项符合性偏离）
- WARNING: Y 项
- INFO: Z 项

## 符合性检查结果
### [CF-001] [文件路径] 与 [审批产物路径#章节] 不一致
- 审批产物要求：...
- 实际实现：...
- 建议修复：...

## 通用质量问题
### [C-001] [文件路径:行号] [问题标题]
- 问题描述：...
- 建议修复：...

## 整体评价
[总结代码质量 + 与审批产物的整体一致性]
```

如果 docs 目录不存在则创建。