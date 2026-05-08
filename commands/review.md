对当前项目进行代码审查：

$ARGUMENTS

如果未指定审查范围，则审查最近 24 小时内的所有 commit。

## 1. 风险与审查深度

风险分级直接遵循 `rules/product-dev.mdc`，不要在本命令里另写一套判定标准。输出使用：

- `low`
- `normal`
- `high`

审查模式：

- `concise`：低风险 / 常规风险默认模式，只找阻塞问题、真实风险、缺失验证。
- `full_conformance`：仅高风险或用户明确要求严格符合性审查时使用。

## 2. 按需读取基线

只有高风险审查、变更显式触达这些基线，或存在常规风险 spec delta 时，才读取：

1. `docs/spec-delta-*.md` — 常规风险行为变更的最小 intent 载体，只用于理解意图。
2. `docs/approved/` — 高风险人工审批基线。
3. `.testing/user-stories/` — User Story 与验收标准。
4. `CLAUDE.md` — 项目架构约束。

加载 `docs/approved/` 时检查 frontmatter：

- `approved_by: pending`：标注为未审批产物，不作为符合性基线。
- 缺少元数据头：标注为缺少审批元数据，仍可参考但降低置信度。
- `approved_by` 为具体人名：作为符合性基线。

## 3. 审查顺序

按 `Intent → Code → Validation` 分析：

1. 先确认变更意图是否清楚且未超范围。
2. 再看代码是否符合意图与既有架构。
3. 最后看验证证据是否覆盖关键正向、负向和回归场景。

所有风险等级都做通用质量检查：

- 代码质量：逻辑错误、边界问题、异常处理、命名、无谓复杂度。
- 安全性：密钥、注入、XSS、路径穿越、权限边界、输入验证。
- 测试覆盖：新增功能测试、bug 复现测试、负向场景、禁止存在性测试。
- 架构一致性：分层依赖、公共契约同步、Web surface 对齐或 `no-web-impact` 说明。
- 可维护性：非冗余注释、TODO/FIXME/HACK、跨模块影响。
- 设计质量：Jobs 简洁、最小 API 面、聚焦边界、OPC 自动化、流程极简。

仅在 `full_conformance` 中增加逐项符合性检查：代码 ↔ 设计文档 / API 契约 / 验收标准 / 技术选型 / 任务边界。每个 conformance finding 必须引用审批产物路径和章节。

## 4. 默认输出：只服务当下决策

默认只在对话中输出精简结果，不创建 `.reviews/*.json` 或 `.reviews/*.md`：

```text
risk_level: low | normal | high
review_mode: concise | full_conformance
decision: merge-ready | needs-fix | needs-design-review
validation_gaps: none | [...]
findings:
- [R-001] severity category file:line — description
  suggested_fix: ...
```

默认只输出值得打断作者的发现：

- `critical` / `high`：默认输出。
- `medium` / `low`：仅在确实值得立即处理时输出。

没有发现阻塞问题时，直接给 `merge-ready`，不要生成空报告文件。

## 5. 持久化：PR comment 优先，本地文件按需

持久化只在以下情况发生：

- 用户明确要求“严格审查 / 留档 / record / 生成 JSON”。
- `review_mode=full_conformance` 且需要保留完整证据链。
- 用户要求写入 PR，或用户接受某条 finding 后要求记录到 PR。

### PR comment 是默认校准载体

需要持久化且存在 GitHub PR 时，优先把 finding 写成 PR comment / review note，而不是本地孤儿文件。写 PR comment 属于共享状态：除非用户已经明确要求，否则先确认。

每条可被校准的 finding 在评论正文末尾带一个隐藏 marker：

```markdown
<!-- dev-rules-review: {"schema_version":1,"id":"R-001","severity":"high","category":"validation","file":"src/foo.py","line":42,"risk_level":"normal","review_mode":"concise","tool":"claude"} -->
```

正文给人读，marker 给 `/user:calibrate` 读。marker 只放事实字段，不塞长描述；长描述留在正文，避免 PR comment 变成 JSON 仓库。

### 本地 JSON 只是严格/离线备选

只有用户明确要求本地记录时，才输出 `.reviews/review-$(date +%Y%m%d).json`。该 JSON 必须通过 `dev-rules/schemas/review.schema.json`。`human_verdict` 仅用于离线校准 fallback；日常闭环优先来自 PR comment、回复、后续 diff、merge/close 状态。

Markdown 摘要只在以下情况生成：

- `review_mode=full_conformance` 且用户需要人类可读留档。
- 用户明确要求贴进 PR comment / review note。
- 需要从本地 JSON 生成离线审查记录。
