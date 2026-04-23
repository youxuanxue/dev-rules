# 数字分身系统：哲学基石与硬约束

> 本文档只回答 **why** 和 **what 必须机械保证**，不重复 **how**（操作流程在 `rules/`、`commands/`、`global/CLAUDE.md`、`README.md` 中，与本文冲突以它们为准）。
>
> - 想知道「为什么这个系统这样设计」→ §一
> - 想知道「每条软规则被哪段脚本拦截」→ §二
> - 想知道「文档里的数字为什么不会过期」→ §三
>
> 这种分工本身就是 Jobs（一个文件只做一件事）+ OPC（操作真相由可执行脚本承载，不由散文承载）的体现。

---

## 一、两个哲学基石

本系统的一切设计决策建立在两个互为支撑的哲学之上。

### 乔布斯产品设计哲学 — 决定「做什么」与「怎么做」

> "People think focus means saying yes to the thing you've got to focus on. But that's not what it means at all. It means saying no to the hundred other good features that there are." — Steve Jobs

**核心原则**：

1. **聚焦**：对一千件事说不，只做一件做到极致的事。每多一个功能，复杂度的增长是指数级的。
2. **简洁**：简洁是复杂的最终形态。代码、API、UI 都应追求「看起来显然没有多余的东西」。
3. **端到端体验**：不是把各模块做好再拼凑——用户感知的是完整旅程。
4. **设计即工作方式**：设计不是表面美观，而是产品如何工作。一个好 API 应该让调用者觉得「就该是这样」。
5. **精品意识**：宁可少做，做出来的每一个功能都必须达到「可以自豪地展示」的水准。

**对本系统的具体约束**：

- 每个文件、规则、脚本必须挣得自己的位置；说不出价值的直接删除。
- 高风险变更先回答「这个功能应不应该存在」，再回答「怎么实现」。
- 默认单 PR 单一意图；只有真实决策边界或风险隔离才拆 PR（见 `rules/product-dev.mdc`）。
- WebUI/API/CLI/MCP 表面最小化：每个导出符号（含 UI 页面与组件）必须有真实消费者，"以备将来" 的导出禁止存在（见 `rules/agent-contract-enforcement.mdc`）。

### OPC（One-Person Company）哲学 — 决定「谁来做」与「做多少」

> 一个人 + AI 数字分身 = 一个精干团队的产出。不是缩小版的大公司，而是完全不同的运转方式。

**核心原则**：

1. **杠杆最大化**：人的时间是唯一不可扩展的资源。每件事先问「这必须由人做吗」——不是就交给 Agent。
2. **流程极简**：每个流程步骤必须挣得自己的位置；不能证明价值的步骤就是损耗。
3. **自动化优先**：能自动化的绝不手工。手工只在需要人类判断力的关键节点存在（审批门禁、架构决策）。
4. **深度 > 广度**：不做十个平庸的产品，做一个深入骨髓的产品。OPC 的竞争力不是人多，而是在一个点上做得比任何大团队都深。
5. **反脆弱**：规则、记忆、技能全部代码化、版本化（`dev-rules/`），不依赖任何一个人的记忆或任何一台机器。

**对本系统的具体约束**：

- 人工介入只出现在高风险审批门禁与架构决策；其他一切可自动化。
- 任何"靠自觉"的软规则若反复失守，**必须**升级为机械检查（见 §二）。
- 散文档中的数值/事实声明若会随实现演进而过期，**必须**用 stat 块包裹（见 §三）。
- 不招人，不组团队。产能瓶颈靠增加 Agent 并行数解决，不靠增加人头。

### 两个哲学的交汇

```
乔布斯哲学                              OPC 哲学
  │                                       │
  │ 做什么：聚焦一个产品                     │ 谁来做：1 人 + N 个 Agent
  │ 怎么做：简洁、端到端、精品              │ 做多少：杠杆最大化、流程极简
  │                                       │
  └──────────┬────────────────────────────┘
             │
    本系统的设计原则：
    · 产品方向你来定（聚焦），执行 Agent 来做（杠杆）
    · 做少做精（乔布斯），自动化一切非判断性工作（OPC）
    · 高风险审批门禁 = 人类判断力的最小必要介入
    · Agent 的 Rules/Skills/Memory = OPC 的「制度资产」
```

### 哲学落地的硬约束机制

哲学不是用来挂在墙上的——本系统的关键纪律是**每条软规则必须配套一个机械检查脚本**，违反时构建/提交/CI 真的会失败。

- **「自动化优先」的元约束**：当某个"靠自觉"的问题反复出现，必须新增一段检查到 `scripts/preflight.sh` 或 `verify-rules.sh`。
- **「变更必伴漂移」的元解法**：先问"数字真的需要写吗"——多数情况答案是否，直接删；只有当数字本身是设计契约（cap / SLO / 预算）时，才用 `<!-- stat:NAME -->` 占位符 + `sync-stats.sh --check` 机械拦截（见 §三）。
- **「承诺必须等于事实」的元约束**：凡是文档/规则里声称"自动跑"的进程（如跨机器同步 LaunchAgent），必须有一段检查验证它真的在运行；`verify-rules.sh` 的 LaunchAgent 实装段是这条原则的样板。

完整的软→硬映射见 §二。

---

## 二、软规则 → 硬检查映射

本表是单一事实来源，rules 与 commands 中的引用都指向这里。每一行都对应一段会真的让 `git commit` 失败的脚本——若某行没有"硬约束实现"列，它就不该出现在这里。

| 软规则（描述于）                                                                                                          | 硬约束实现                                                                                             | 触发时机                 | 失败后果                         |
| ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | -------------------- | ---------------------------- |
| `.cursor/rules/` 与 submodule 同步 (`dev-rules-convention.mdc`)                                                      | `dev-rules/sync.sh --check`                                                                       | pre-commit / CI      | exit 1，阻断提交                  |
| dev-rules 仓库自身完整性（README / frontmatter / 哲学映射 / 幽灵路径 / global 关键文件 / LaunchAgent 实装） (`dev-rules-convention.mdc`) | `dev-rules/verify-rules.sh`                                                                       | 子模块提交前               | exit 1                       |
| 先子模块后父仓库（submodule SHA 必须在 dev-rules 中真实存在） (`dev-rules-convention.mdc`)                                          | `scripts/preflight.sh` 段 2                                                                        | pre-commit / CI      | exit 1                       |
| review JSON 输出格式合规 (`review.md`)                                                                                  | `dev-rules/schemas/review.schema.json` + ajv/check-jsonschema                                     | `/user:calibrate` 入口 | 该 JSON 排除出校准                 |
| WebUI/API/CLI/MCP 契约不漂移 (`agent-contract-enforcement.mdc`)                                                          | `python scripts/export_agent_contract.py --check`                                                 | preflight 段 4 / CI   | exit 1                       |
| 分支命名前缀（`prototype/` `feature/` `fix/` `chore/` `docs/` `merge/` `cursor/`） (`product-dev.mdc`)                    | `scripts/preflight.sh` 段 1                                                                        | pre-commit           | exit 1                       |
| User Story ↔ Test 不漂移 (`test-philosophy.mdc`)                                                                     | `python .testing/user-stories/verify_quality.py`                                                  | preflight 段 5 / CI   | exit 1                       |
| `docs/approved/` 在非高风险 / 非原型路径中被修改时必须触发 reviewer 明确确认 (`product-dev.mdc`)                                          | `scripts/preflight.sh` 段 6                                                                        | PR 检查                | warn → reviewer 必须确认         |
| `approved_by: pending` 不进 main (`product-dev.mdc`)                                                                | `scripts/preflight.sh` 段 7                                                                        | main 分支 commit / CI  | exit 1                       |
| 提交前完成自检 (`product-dev.mdc`)                                                                                       | 整个 `scripts/preflight.sh`（接到 git pre-commit hook）                                                 | pre-commit / CI      | exit 1                       |
| 文档/规则中引用的 `dev-rules/...` 路径必须真实存在（防"幽灵引用"） (`dev-rules-convention.mdc`)                                          | `dev-rules/verify-rules.sh` 幽灵路径段                                                                 | 子模块提交前               | exit 1                       |
| preflight 真的会被自动触发，不依赖人记得跑 (`dev-rules-convention.mdc`)                                                           | `dev-rules/templates/install-hooks.sh` 安装 git pre-commit hook                                     | 项目接入时一次              | hook 缺失则 commit 不会触发检查       |
| Claude Code 全局宪法（`~/.claude/CLAUDE.md`）不是手维护的孤儿文件 (`dev-rules-convention.mdc`)                                    | `dev-rules/global/CLAUDE.md` + `sync.sh` 自动 symlink                                               | 跨会话持续                | 链接断裂时 sync.sh `--status` 标 ⚠ |
| 本机 dev-rules mirror 不静默落后远端（"承诺有 LaunchAgent" ≠ "实际装了"） (`dev-rules-convention.mdc`)                              | `dev-rules/templates/install-launchagent.sh` 注册 + `dev-rules/verify-rules.sh` 的 LaunchAgent 实装段检查 | 子模块提交前 + 30 min 自动跑  | 未装 → verify-rules.sh exit 1  |
| dev-rules 编辑后所有消费端及时更新（不依赖人记住 N 个 sync 命令）                                                                        | `dev-rules/sync.sh --push`（push + ~/Codes pull + 所有项目 fan-out 原子动作）                               | 编辑者主动                | 不跑则下一次 LaunchAgent 兜底        |
| 设计契约型数字（cap / SLO / 预算等）不漂移 (本文档 §三)                                                                                  | `dev-rules/sync-stats.sh --check`（基于 `.stats.json` 注册表 + 文档内 `<!-- stat:NAME -->` 占位符；仅服务真实契约，禁止虚荣计数）  | preflight 段 8 / CI   | exit 1                       |
| 云端 Agent / 本地 Agent 运行环境一致（CLI、secrets、Claude gateway） (`product-dev.mdc` §云端 Agent)                              | `dev-rules/templates/cloud-agent-bootstrap.sh --check`（读 `.cursor/cloud-agent.env`）               | preflight 段 9 / CI / 云端 install | exit 1（REQUIRED 缺失）           |

**升级原则**：当 review 中反复发现某个"靠自觉"的问题时，**必须**新增一段检查到 `preflight.sh` 或 `verify-rules.sh`，把软约束硬化。这条本身写入了 `product-dev.mdc` 的「完成自检」节末尾，并由 `verify-rules.sh` 的哲学映射段检查上表中每条规则都有对应脚本，禁止纯描述。

**新项目接入**：见 `rules/dev-rules-convention.mdc`「强约束门禁」节。

---

## 三、反漂移机制（仅服务于设计契约，不服务于虚荣计数）

### 元原则先于机制：Jobs 的第一反应是「为什么写数字」

文档里的每一个数字都是一次"承诺"——承诺它会与实现保持一致。Jobs 会先问："**这个数字读者真的需要看见吗？**"

- "verify-rules.sh **检查仓库完整性**" ← 信息完整
- "verify-rules.sh **现在 8 段** 检查" ← 多了一个数字承诺，没多一点信息

绝大多数计数型数字（"X 段 / X 条 / X 行 / N 个"）属于第二种：**写它们 = 自找麻烦**。删掉它们，段落仍完整传达意图，且不再需要任何反漂移机制。

### 真正需要机器盯着的：设计契约

少数情况下，数字本身**就是**规则——不只是描述实现现状，而是约束实现不能突破：

| 数字的角色 | 例子 | 是否值得机制化 |
| --- | --- | --- |
| 设计上限（cap） | "WebUI 核心场景页面数 ≤ 8"（zw-brain）；"自动修复轮数 ≤ 2" | ✅ 是 |
| 阈值/SLO | "审查准确率达标门槛 ≥ 80%" | ✅ 是 |
| 预算 | "单次审查预算 ≤ $5" | ✅ 是 |
| 当前实现的计数 | "verify-rules.sh 8 段" "本表 17 行" "5 条规则" | ❌ 否——是描述不是契约 |

**判别公式**：删掉这个数字，段落是否仍然完整传达意图？是 → 删掉数字（首选）；否 → 它是契约 → 才进入下面的机制（保底）。

### 机制（仅给真正需要的契约用）

两件工件，配合使用：

1. `dev-rules/.stats.json` —— 数值事实注册表，每条声明定义 compute 命令：

   ```json
   "zwbrain.webui-pages-cap": {
     "description": "zw-brain 设计基线 §7.3 表中声明的 WebUI 页面数（设计契约：≤ 8）",
     "compute": "..."
   }
   ```

2. `dev-rules/sync-stats.sh` —— 三种模式：
   - `--list`   列出所有 stat 当前 live 值
   - `--update` 扫描所有 `*.md` `*.mdc`，把 `<!-- stat:NAME -->...<!-- /stat -->` 的内部值替换为 live 值
   - `--check`  对比，发现漂移即 exit 1（接 preflight 段 8）

文档侧语法：`<!-- stat:NAME -->VALUE<!-- /stat -->`。

### 注册的硬门槛（防止机制本身被滥用）

凡新增到 `.stats.json` 的 stat，必须同时满足：

1. **compute 命令在干净 checkout 上稳定可重现**——脆弱的 awk/grep 模式禁止
2. **至少有一处文档使用它**——孤儿 stat 必须删除（曾经的 `preflight-sections` `rules-count` `commands-count` 就是反例，已清理）
3. **它表达的是设计契约，不是虚荣计数**——如果只是"当前有 N 个"，删掉数字本身比注册 stat 更好

dev-rules 自己一度注册了 5 个 stat，全部都是当前实现的计数，没有一个是设计契约——已全部删除。这一节的存在主要是为了给 zw-brain 那种"设计基线里写明上限"的真实契约场景兜底。

### 不做的取舍

- 不做 LLM 文档一致性检查（不可验证、贵、慢）
- 不做全文档生成（散文叙述本身有价值，过度模板化反损可读性）
- 不做跨文件交叉引用 lint（rabbit hole）
- 不为虚荣数字注册 stat（这条本身是 OPC 元约束：宁可让某个数字漂移一会儿被发现后删掉，也不要为了一个不该存在的数字搭建一套校验流水线）

---

## 四、本文档不覆盖的内容

操作 SOP（仓库内容、接入、日常工作流、自检纪律、测试方法、命令、云端环境……）见 `README.md` 与 `rules/`、`commands/`、`global/`、`templates/`。本文件只承担 §一-§三 的"why + 元约束"；冲突时以那些文件为准。
