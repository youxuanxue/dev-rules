# 完整 Story 路径

命中高风险时读取；无 UI 工件需要展开对外契约风险时只读 §11。日常测试纪律在 `rules/test-philosophy.mdc`。

## 10. 故事推导（测什么）

从产品结构推导，不靠经验拍脑袋。

1. **角色 × 能力**：每个角色在每个核心能力上的动作 → 故事候选。
2. **实体生命周期**：每条状态迁移边（含失败回退边）→ 必须有故事。
3. **系统事件**：定时任务/Webhook/阈值触发/过期处理 → 必须有故事。
4. **防御需求**：每个能力的误用/滥用约束 → 必须有故事。

**覆盖门禁**：角色×能力矩阵无空格、状态迁移无漏边、系统事件无未映射故事。

## 11. 用例设计（四类风险 + 维度矩阵）

每个 InTest/Done 故事必须在 Risk Focus 中声明覆盖了哪些风险，不涉及的须注明原因。

| 风险类型 | 典型场景 |
|---|---|
| 逻辑错误 | 参数处理、鉴权判定、计费一致性、状态迁移 |
| 行为回归 | 返回结构/字段语义变化、关键链路中断、跨模块契约破坏 |
| 安全问题 | 越权访问、白名单误判、绕过防护、缺少二次校验 |
| 运行时问题 | 重启恢复、超时传播、重试幂等、并发竞争、资源耗尽 |

| 维度 | 何时展开 |
|---|---|
| 正向路径 | 始终 |
| 输入空间 | 行为变更或输入校验相关 |
| 前置状态 | 状态机、迁移、缓存、重试 |
| 副作用 | 有副作用时 |
| 并发时序 | 有写操作或幂等要求时 |
| 权限角色 | 有权限区分或安全风险时 |

按风险选择需要展开的维度；不是每个维度都默认补 case。

## 12. 故事落盘规范

- 根路径：`.testing/user-stories/`（项目目录内）；索引：`.testing/user-stories/index.md`；故事：`stories/US-<3位编号>-<kebab-case>.md`；证据：`attachments/`。
- 必填字段：ID、Title、As a / I want / So that、Trace、Risk Focus、Acceptance Criteria（含正向 + 负向 + 回归）、Assertions、Linked Tests（`file::TestFunction` + 可执行运行命令）、Evidence、Status（Draft → Ready → InTest → Done / Archived）。

```markdown
# US-XXX-slug
- ID: US-XXX
- Title: ...
- As a / I want / So that: ...
- Trace: [来源轴线]
- Risk Focus:
  - 逻辑错误：[场景]
  - 行为回归：[场景]
  - 安全问题：[场景] 或 "不适用：[原因]"

## Acceptance Criteria
1. AC-001 (正向): Given ... When ... Then ...
2. AC-002 (负向): Given ... When ... Then 返回错误并拒绝...
3. AC-003 (回归): Given 代码变更 When 执行 TestUSXXX_* Then 全部通过

## Assertions / Linked Tests / Evidence / Status
```

## 13. Story ↔ Test 对齐

- 命名：测试函数 `TestUSxxx_ScenarioName`，文件 `usXXX_slug_test.go`。
- Linked Tests 格式 `file::TestFunction`，禁止只引用实现文件，每条故事至少一条可执行运行命令。
- **漂移检测**（每次提交）：每个 AC → 有测试函数覆盖；Linked Tests 引用真实可运行；声明的 Risk Focus 必须有对应负向/边界断言。
- 项目维护 `.testing/user-stories/verify_quality.py` 时，preflight 自动跑：可执行命令 ✓ | 负向场景 ✓ | 可观测断言 ✓ | 风险类别 ✓。报告输出到 `.testing/user-stories/attachments/story-quality-report.md`。
