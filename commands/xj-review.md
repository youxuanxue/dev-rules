对当前项目进行代码审查：

$ARGUMENTS

如果未指定审查范围，则审查最近 24 小时内的所有 commit。

## 0. 机械门禁先行（确定性优于肉眼）

在做任何模型判断之前，先跑项目的确定性门禁，把它的结论当作 ground-truth，**不要用模型重新肉眼判断脚本已经机械覆盖的项**。这是「确定性自动化运营和运维」原则：能机械化的检查由脚本承载，模型只补脚本覆盖不到的判断残差。

1. 若项目存在 `scripts/preflight.sh`，先运行它；PR 审查语境（`--base origin/main`）下传 `PREFLIGHT_BASE=origin/main`：

   ```bash
   PREFLIGHT_BASE=origin/main bash scripts/preflight.sh 2>&1 | tee /tmp/xj-review-preflight.txt
   ```

   该脚本已机械覆盖以下原本写在本命令 prose 里的检查（逐项 FAIL 直接转成 finding，无需模型再判断）：契约删除/Web surface 对齐/分层依赖/高风险审批锚点/release skip-ci/workflow 硬失败 pattern/review 与 skill manifest schema/删文件悬空引用/**存在性测试**/`docs/approved` frontmatter 不变量/本地 linter（ruff 等，含**未用 import F401**）。

   若项目用其他名字的等价机械门禁脚本（如 dev-rules 自身的 `verify-rules.sh`），按相同方式调用——结论同样视为 ground-truth。判定优先级：`scripts/preflight.sh` → 项目根的等价脚本（`verify-rules.sh` / `Makefile check` 目标等）→ §0.3 fallback。

2. preflight 每个 `FAIL:` 段 = 一条 finding，severity 至少 `high`，直接进 findings 列表，**置信度高于模型推断**。preflight `PASS` 的维度不再由模型重复质疑。

   warn-only 段（如"silent-error-swallow sites"列出的 `|| true` / `--no-verify` / `except: pass` 点）= 确定性候选清单：模型逐项判断是否掩盖真实失败（合法 cleanup 放过，否则升级为 finding）。机械保证的是**召回**（不漏点），判断仍由模型做。

3. 上述脚本均不存在、或某检查 `skip`（前置工件缺失）时，该维度才回退到模型判断，并在 finding 里注明"机械门禁缺位"——按下方「确定性自动化运营和运维」准则，这本身可能就是一条 finding。

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
- 设计质量与确定性自动化运营和运维：是否违背 Jobs 哲学或 OPC 原则——具体判据与必报清单见下方严格 merge-ready 的「Jobs 哲学违背」「OPC 原则违背」两条（同一标准适用所有风险等级，只是 PR 强模式下必报），此处不再重述。

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
- **确定性自动化运营和运维原则违背必须列入 finding**：流程依赖人记忆（"以后注意"、"下次记得"）、`|| true` 类静默吞错、本可机械化检查但写成 prose 规则、preflight / hook / 自动化缺位、提交一次发现的问题不固化为 check。
- **方向校准（发上面两类判断 finding 前先做）**：先把这条 finding 对齐 global `CLAUDE.md §1` 的 OPC 原则。**若一条 finding 会减少自动化 / 自主闭环 / 杠杆**（建议把自动步骤改回人工、拆掉闭环、加审批摩擦、为"更可控"砍掉 agent 自主性），在 OPC 下它几乎必然是反的——OPC 偏好更自主的闭合，不是更多人工。最坏的 finding 不是漏报，是反方向推 PR；方向拿不准时降级为提问，不要直接发成 finding。
- **循环直到收敛是 agent 默认行为，不是把责任甩给用户**。发 `needs-fix` 后立即进入修复闭环（见下），不要止步于"输出 finding 等用户处理"。

### needs-fix → 修复闭环（默认 agent 主动推进）

确定性自动化运营和运维：reviewer 工作不是发现问题，而是把 PR 推到可合并状态。`needs-fix` 触发以下默认动作，**不再等用户单独发出"请修复"指令**。

**循环计数与熔断不由模型手数。** 四个阈值——大循环（§115/§128 合并预算）、同一脚本连续失败、同一 finding 未修掉、同一 CI job 连续失败——全部由确定性脚本 `loop_state.py` 派生（阈值是查表，正属「确定性自动化运营和运维」必须脚本化的类目）。模型只在决策点调用它并**逐字转发** `verdict=`；读到 `verdict=halt` 立即 stop-the-line（全局 `CLAUDE.md` §2）：把 `reason=` 明示给用户、停止循环，不要自己判断"第几轮"。脚本在 dev-rules submodule 内、自包含可直接按路径执行——下文统一写 `dev-rules/scripts/review/loop_state.py`（**审 dev-rules 仓库自身时去掉 `dev-rules/` 前缀**）；状态存 `/tmp`，按 `--key` 隔离。`--key` 用 PR 标识（如 `owner/repo#123`），进入闭环时初始化一次：

```bash
python3 dev-rules/scripts/review/loop_state.py init --key <owner/repo#PR>
```

1. **每轮开始登记大循环**：`python3 dev-rules/scripts/review/loop_state.py round-start --key <K>`，转发 `verdict`；halt 即停。
2. **逐 finding 修**：按 `critical → high → medium → low` 顺序处理；同级按 R-编号。`suggested_fix` 是参考，不是契约——以理解问题本质优先。
3. **每轮必跑机械门禁**：复用 §0 定义的判定（`scripts/preflight.sh` 优先，无则项目等价物如 `verify-rules.sh`），加上项目 unit/integration 测试套件与相关 linter。每跑一个门禁脚本登记一次结果，失败就修：
   ```bash
   python3 dev-rules/scripts/review/loop_state.py record --key <K> --kind script --id <preflight|pytest|ruff|...> --outcome <pass|fail>
   ```
4. **commit + push**：commit message 推荐 `fix(scope): address R-001..R-NNN — <summary>` 形态；遵循项目的 commit marker 规则（`no-web-impact` 等）。**禁止** `--amend` 已 push 的 commit、`git push --force`、跳 hook（紧急回滚由用户授权后单独走）。
5. **自我 re-review**：重跑本命令 §0-§3。每条仍未修掉的 finding 登记一次，直到 `decision: merge-ready` 且零 medium+ finding 再进入下一节：
   ```bash
   python3 dev-rules/scripts/review/loop_state.py record --key <K> --kind finding --id R-NNN --outcome <pass|fail>
   ```
6. **熔断**：以上任一 `round-start` / `record` 返回 `verdict=halt` 即暂停并明示 `reason=`，等用户介入。脚本已把 §115 与 §128 合并为单一 ≤3 的大循环预算（`fix → CI fail → fix` 整条链共享，见下节），模型不再无限循环、也不再手数——同一问题反复失败的 stop-the-line 由脚本机械保证。

修复期间用户给新指令永远 trumps 这个闭环。修复需要破坏性动作（删数据、动他人分支、`--force`、跳 hook）才向用户确认；普通 code/test/doc 改动直接做。

### merge-ready 之后：follow through 到 CI 全绿

`merge-ready` 不是 reviewer 的终点。在 PR 上下文里它只是"代码本身可以合"，**PR 真正进入待合并要 CI 全绿**。这一段也是默认 agent 闭环，不是甩给用户的 checklist：

1. **盯 CI**：审查的是 GitHub PR 时，立即用 `Monitor` 工具跟踪 `gh pr checks <num>` 直到**所有非 `skipping`/`pending` 的 check** 进入终态。**不要** 仅靠 GitHub branch protection 的 required list——仓库可能未配，required 列表为空时就会漏盯整个 CI。**不要** sleep 轮询；用 per-occurrence 通知驱动（Monitor 工具的天然契约）。
2. **CI 全绿** → 告诉用户："PR #N 已 merge-ready 且 CI 全绿，等你的合并指令"。
3. **CI 失败** → 立即诊断，**不允许** "finding 已修就交付、CI 留给以后"。每个进入终态的 CI job 登记一次（复用上节同一 `--key`，大循环预算自动合并，无需手数）：
   ```bash
   python3 dev-rules/scripts/review/loop_state.py record --key <K> --kind ci-job --id <job-name> --outcome <pass|fail>
   ```
   - **瞬态故障**（runner 拉 PR merge ref 认证失败 / 镜像 503 / 网络 timeout 等，且代码侧未触达该 job 范围）：直接 `gh run rerun --failed`，继续监控，**不**登记为 fail。瞬态判定标准 = "同一 commit、同一 job 配置的其他平行 job 全过" + "故障描述指向 infra 而非代码"。
   - **真实失败**：登记 `--outcome fail`，把失败 job 当成新 finding，回到上一节修复闭环。
4. **熔断**：`record --kind ci-job` 返回 `verdict=halt`（同一 job 连续失败到上限）即暂停并明示 `reason=`。§115 / §128 的预算已在脚本里合并为单一 ≤3 的大循环，由 `round-start` 统一推进——`fix → CI fail → fix` 整条链不会因分两节而各拿一份 3 轮额度。
5. **用户打断永远 trumps**：等 CI 期间用户改主意（"算了别等了" / "先去做其他事"）随时可以暂停或切走，与修复闭环节同步。
6. **永远不调 `gh pr merge`**：合并属于用户授权动作，由 `~/.claude/settings.json` PreToolUse hook 机械兜底；规则层这里只做语义对齐。即使过去用户给过类似 PR 的合并授权，本次也必须等本次的明确指令。

## 5. 持久化：PR comment 优先，本地文件按需

持久化只在以下情况发生：

- 用户明确要求“严格审查 / 留档 / record / 生成 JSON”。
- `review_mode=full_conformance` 且需要保留完整证据链。
- 用户要求写入 PR，或用户接受某条 finding 后要求记录到 PR。

### PR comment 是默认持久化载体

需要持久化且存在 GitHub PR 时，优先把 finding 写成 PR comment / review note，而不是本地孤儿文件。写 PR comment 属于共享状态：除非用户已经明确要求，否则先确认。

PR comment 正文给人读，不再注入隐藏 JSON marker——marker 的唯一消费者（历史校准命令）已删除，没有 consumer 还主动注入是「确定性自动化运营和运维」禁止的"过早工具化"。后续如确实需要重启校准，再统一讨论 marker 形式。

### 本地 JSON 只是严格/离线备选

只有用户明确要求本地记录时，才输出 `.reviews/review-$(date +%Y%m%d).json`。该 JSON 必须通过 `dev-rules/schemas/review.schema.json`。`human_verdict` 仅用于离线校准 fallback；日常闭环优先来自 PR comment、回复、后续 diff、merge/close 状态。

Markdown 摘要只在以下情况生成：

- `review_mode=full_conformance` 且用户需要人类可读留档。
- 用户明确要求贴进 PR comment / review note。
- 需要从本地 JSON 生成离线审查记录。
