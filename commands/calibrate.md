汇总审查校准指标，输出校准信号；只有在用户明确要求严格评估时，才判断是否可以进入下一个 Phase：

$ARGUMENTS

## 输入

读取 `docs/review-*.json` 中所有审查报告，提取其中已标注 `human_verdict` 的 findings。

如果未指定范围，则汇总所有 review JSON；如果指定了日期范围（如 `2026-04-01 2026-04-15`），则只统计该范围内的报告。

这个命令默认是**校准信号**，不是日常研发的硬性阶段门禁。只有当用户明确想做严格评估时，才把阈值当作准入线。

### 强约束：JSON Schema 校验（前置步骤）

在汇总指标前，对每个 `docs/review-*.json` 用 `dev-rules/schemas/review.schema.json` 做 JSON Schema 校验：

```bash
# 推荐工具：ajv-cli（Node）或 check-jsonschema（Python）
ajv validate -s dev-rules/schemas/review.schema.json -d "docs/review-*.json"
# 或
check-jsonschema --schemafile dev-rules/schemas/review.schema.json docs/review-*.json
```

- 校验失败的文件**必须排除出本次校准**，并在报告"数据完整性"小节列出
- 如果校验失败的文件超过 30%，直接退出并输出"数据质量过低，先修复 review 输出格式再校准"
- 校验通过率本身作为一项可观测指标记入 JSON 摘要

注意：`review.schema.json` 现在区分两种审查模式：

- `review_mode=concise`：默认精简审查，可不包含 `approved_artifacts_referenced` / `conformance_summary`
- `review_mode=full_conformance`：高风险审查，必须包含符合性字段

## 计算逻辑

### 指标定义

从所有已标注 `human_verdict` 的 findings 中计算：


| 指标       | 公式                                                                       | 达标门槛  |
| -------- | ------------------------------------------------------------------------ | ----- |
| 审查准确率    | `accurate=true` 的 🟡🔴 findings / 所有已标注的 🟡🔴 findings                   | ≥ 80% |
| 自动修复安全率  | `autofix_safe=true` 的 findings / 所有 `automatable=true` 且已标注的 findings    | ≥ 95% |
| 分级准确率    | `severity_correct=true` 的 findings / 所有已标注的 findings                     | ≥ 75% |
| 误报率      | `accurate=false` 的 findings / 所有已标注的 findings                            | ≤ 20% |
| 符合性检查准确率 | `accurate=true` 的 conformance findings / 所有已标注的 conformance findings     | ≥ 85% |
| 符合性检查覆盖率 | 仅从 `review_mode=full_conformance` 的报告里，基于 `conformance_summary.artifacts_checked` 和 conformance findings 推算 | ≥ 70% |

以上门槛默认作为**参考线**而非硬阻断；只有在用户明确要求“严格校准”时，才用它们给出通过/未通过判断。


### 分级映射

根据 finding 的 `severity` 和 `automatable` 字段映射到分级标签：

- 🟢：`severity=low` 且 `automatable=true`
- 🟡：`severity=medium`，或 `severity=low` 且 `automatable=false`
- 🔴：`severity=high` 或 `severity=critical`

### 样本量检查

如果已标注的 findings 总数 < 20，在结论中标注"样本量不足，建议继续积累后再评估"。

## 输出

输出到 `docs/calibration-$(date +%Y%m%d).md`：

```markdown
# 审查校准报告

> 生成日期：[日期]
> 数据范围：[最早报告日期] ~ [最晚报告日期]
> 审查报告数：N 份
> 已标注 findings 总数：M / 总 findings K

## 指标汇总

| 指标 | 当前值 | 门槛 | 状态 |
|------|--------|------|------|
| 审查准确率 | XX% | ≥ 80% | ✅ / ❌ |
| 自动修复安全率 | XX% | ≥ 95% | ✅ / ❌ |
| 分级准确率 | XX% | ≥ 75% | ✅ / ❌ |
| 误报率 | XX% | ≤ 20% | ✅ / ❌ |
| 符合性检查准确率 | XX% | ≥ 85% | ✅ / ❌ |
| 符合性检查覆盖率 | XX% | ≥ 70% | ✅ / ❌ |

## 典型误报分析

[列出 accurate=false 的 findings，按 category 分组，分析误报模式]

## 典型漏报分析

[如果 human_verdict.notes 中提到了 Claude Code 未发现的问题，汇总列出]

## 分级偏差分析

[列出 severity_correct=false 的 findings，分析 Claude Code 倾向于高估还是低估]

## 结论

**严格校准模式下的准备状态：✅ 已就绪 / ❌ 未就绪**

[如果未就绪，列出未达标指标及改进建议（如调整 review prompt、补充 rules 等）]

[如果样本量不足，说明需要继续积累]
```

同时输出 JSON 格式到 `docs/calibration-$(date +%Y%m%d).json`，包含所有原始计算数据，便于趋势追踪。

## 注意事项

- 只统计有 `human_verdict` 且至少填了 `accurate` 字段的 findings
- 跳过 `human_verdict` 为空 `{}` 的 findings（未标注）
- 如果没有任何已标注的 findings，输出提示"尚未进行人工校准，请先标注 review JSON 中的 human_verdict"
- `review_mode=concise` 的报告可用于通用准确率、误报率、分级准确率统计；只有 `review_mode=full_conformance` 的报告参与符合性覆盖率统计

