将以下需求拆解为可独立执行的子任务：

$ARGUMENTS

## 步骤

1. **聚焦过滤（Jobs）**：先回答「核心场景是什么」+「不做什么、为什么」。砍掉的功能和保留的功能同等重要。
2. **风险判定**：直接套用 `rules/product-dev.mdc` 的低/常规/高风险定义，**逐条按其原文比对，不要凭记忆复述或另写一套**。输出 `低风险` / `常规风险` / `高风险` 三选一。
   - 若本次拆解针对**已存在改动**（有 diff base，例如重新评估一个进行中分支），先跑已有的确定性高风险信号，把命中项当作客观依据而非靠模型嗅探：
     ```bash
     B=origin/main
     python3 dev-rules/scripts/check_high_risk_anchor.py --base "$B"        # 高风险路径动了没绑审批锚点
     python3 dev-rules/scripts/check_contract_deletion_notice.py --base "$B" # 公共契约删除
     python3 dev-rules/scripts/check_web_surface_alignment.py --base "$B"    # 后端改动未对齐 Web surface
     ```
     任一非零退出 = 命中对应高风险条件的客观证据，写入「命中的风险条件」。
   - 纯规划（尚无 diff）时无客观信号可跑，按 product-dev.mdc 五条高风险条件逐条判断；其中「高回滚成本骨架」「核心体验高爆炸半径」属判断项，拿不准默认降为常规风险并补澄清问题（遵循 product-dev.mdc 第 65 行）。「改动文件多/代码量大/多模块」**不能单独**判高风险。
3. **意图载体**（按风险选最小）：
   - 低风险：对话内拆解，**不留档**。
   - 常规风险：默认 PR summary；行为变化复杂到 PR summary 说不清时才补 `docs/spec-delta-<slug>.md`（Background / Delta / Scenarios / Validation 四块）。
   - 高风险：`docs/approved/<file>.md`（含 `approved_by: pending` frontmatter），并按 `product-dev.mdc` 高风险路径推进。
4. **拆子任务**：每个子任务必须可由一个 Agent 独立完成、有可观测验收、复杂度 S/M/L（>8h 需再拆）。
5. **PR 形状**：默认**同一 PR**；只有真实决策边界或风险隔离时才拆 PR，并在该子任务的 PR 归属里写明拆分原因。
6. **澄清问题**：仅列出 Agent 无法从 repo facts / dev-rules / 现有审批基线推断的真问题。

## 子任务字段

`标题 | 目标 | 验收标准 | 复杂度 | 依赖 | PR 归属`

依赖标注：无依赖写 `[PARALLEL]`；有依赖写 `[SEQUENTIAL: T-编号]`；高风险审批节点写 `[GATE: 需人工审批]`。

引擎默认沿用当前会话环境；只有明显需要无人值守长跑时才切换到 `Cursor Long-running` 或 `Claude Code Headless`，并在子任务里注明切换原因。

## 输出格式

```markdown
# 任务拆解：[需求标题]

> 风险等级：低风险 / 常规风险 / 高风险
> 意图载体：无 / PR summary / docs/spec-delta-<slug>.md / docs/approved/<file>.md
> PR 形状：单 PR / 多 PR（写明拆分原因）

## 风险判断依据
- 命中的风险条件：...
- 未命中的高风险条件：...

## 聚焦决策（不做什么）
| 被排除 | 原因 |
|---|---|
| ... | ... |

## 子任务
### T-001: [标题] [PARALLEL]
- 目标：...
- 验收标准：可运行命令 / 可观测行为
- 复杂度：S/M/L
- 依赖：无
- PR 归属：同一 PR

### T-002: [标题] [SEQUENTIAL: T-001]
...

### ▶ GATE-1: 原型审批 [GATE: 需人工审批]   ← 仅高风险路径
- 审批方式：人在 PR 中编辑 `docs/approved/*` 并 merge

## 待澄清问题
- [ ] ...
```

低风险默认直接在对话中输出；只有用户要求留档、或高风险路径必需，才写入 `docs/task-breakdown-$(date +%Y%m%d).md`。
