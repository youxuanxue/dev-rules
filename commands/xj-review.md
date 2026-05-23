对当前项目进行代码审查：

$ARGUMENTS

如果未指定审查范围，则审查最近 24 小时内的所有 commit。

## 0. 机械门禁先行（确定性优于肉眼）

在做任何模型判断之前，先跑项目的确定性门禁，把它的结论当作 ground-truth，**不要用模型重新肉眼判断脚本已经机械覆盖的项**。这是 OPC 原则：能机械化的检查由脚本承载，模型只补脚本覆盖不到的判断残差。

1. 若项目存在 `scripts/preflight.sh`，先运行它；PR 审查语境（`--base origin/main`）下传 `PREFLIGHT_BASE=origin/main`：

   ```bash
   PREFLIGHT_BASE=origin/main bash scripts/preflight.sh 2>&1 | tee /tmp/xj-review-preflight.txt
   ```

   该脚本已机械覆盖以下原本写在本命令 prose 里的检查（逐项 FAIL 直接转成 finding，无需模型再判断）：契约删除/Web surface 对齐/分层依赖/高风险审批锚点/release skip-ci/workflow 硬失败 pattern/review 与 skill manifest schema/删文件悬空引用/**存在性测试**/`docs/approved` frontmatter 不变量/本地 linter（ruff 等，含**未用 import F401**）。

2. preflight 每个 `FAIL:` 段 = 一条 finding，severity 至少 `high`，直接进 findings 列表，**置信度高于模型推断**。preflight `PASS` 的维度不再由模型重复质疑。

   warn-only 段（如"silent-error-swallow sites"列出的 `|| true` / `--no-verify` / `except: pass` 点）= 确定性候选清单：模型逐项判断是否掩盖真实失败（合法 cleanup 放过，否则升级为 finding）。机械保证的是**召回**（不漏点），判断仍由模型做。

3. preflight 不存在或某检查 `skip`（前置工件缺失）时，该维度才回退到模型判断，并在 finding 里注明"机械门禁缺位"——按下方 OPC 准则，这本身可能就是一条 finding。

4. 脚本天然覆盖不到、需要模型判断的残差（见 §3 与严格 merge-ready 准则）：意图是否超范围、过度抽象、命名复杂度、重复维护、UI 入口过多、文档与代码各讲一遍等设计/语义问题。

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

### 严格 merge-ready 准则（PR 审查上下文必须遵循）

当审查的是一个进行中的 PR（即 `--base origin/main`，目标是判断是否可合并），`merge-ready` 必须**收敛与严格**：

- **零 `medium+` finding**：`critical` / `high` / `medium` 中任一非零都不得发 `merge-ready`，必须 `needs-fix` 并 loop。
- **顺手发现的 out-of-scope 问题必须列入 finding**：审查过程中如果路过看到与本 PR 无关但确实存在的问题（命名混乱、注释陈旧、复制粘贴遗留、零调用函数、未使用的 import / 配置 / 路由），必须列出。理由：reviewer 已经在文件里了，让作者顺手修的成本远低于以后单独开 PR。**不允许"留待后续"作为搪塞**——除非该问题本身够大、值得独立 PR 评审，此时仍需在 finding 中明示"建议开独立 PR"并继续保持 `needs-fix`。
- **Jobs 哲学违背必须列入 finding**：过度抽象（为想象中的未来需求建抽象层）、重复维护（同一信息存两处需手同步）、多此一举的开关 / 配置项 / feature flag、复制粘贴未消除、命名复杂度高于实际语义、UI 入口过多、文档与代码各讲一遍。
- **OPC 哲学违背必须列入 finding**：流程依赖人记忆（"以后注意"、"下次记得"）、`|| true` 类静默吞错、本可机械化检查但写成 prose 规则、preflight / hook / 自动化缺位、提交一次发现的问题不固化为 check。
- **循环直到收敛**：发 `needs-fix` 后用户/作者应循环 fix → re-review 直到达到上述零 finding 状态，才发 `merge-ready`。

### merge-ready 之后必须做什么 / 必须不做什么

- 必须做：在对话中告知用户"已 merge-ready，等待你的合并指令"。
- 必须不做：**直接调用 `gh pr merge`**。合并属于用户授权动作，不在 `/xj-review` 范围内。即使过去用户给过类似 PR 的合并授权，本次也必须等本次的明确指令。这一条由 `~/.claude/settings.json` PreToolUse hook 机械兜底；规则层这里只做语义对齐。

## 5. 持久化：PR comment 优先，本地文件按需

持久化只在以下情况发生：

- 用户明确要求“严格审查 / 留档 / record / 生成 JSON”。
- `review_mode=full_conformance` 且需要保留完整证据链。
- 用户要求写入 PR，或用户接受某条 finding 后要求记录到 PR。

### PR comment 是默认持久化载体

需要持久化且存在 GitHub PR 时，优先把 finding 写成 PR comment / review note，而不是本地孤儿文件。写 PR comment 属于共享状态：除非用户已经明确要求，否则先确认。

PR comment 正文给人读，不再注入隐藏 JSON marker——marker 的唯一消费者（历史校准命令）已删除，没有 consumer 还主动注入是 OPC 禁止的"过早工具化"。后续如确实需要重启校准，再统一讨论 marker 形式。

### 本地 JSON 只是严格/离线备选

只有用户明确要求本地记录时，才输出 `.reviews/review-$(date +%Y%m%d).json`。该 JSON 必须通过 `dev-rules/schemas/review.schema.json`。`human_verdict` 仅用于离线校准 fallback；日常闭环优先来自 PR comment、回复、后续 diff、merge/close 状态。

Markdown 摘要只在以下情况生成：

- `review_mode=full_conformance` 且用户需要人类可读留档。
- 用户明确要求贴进 PR comment / review note。
- 需要从本地 JSON 生成离线审查记录。
