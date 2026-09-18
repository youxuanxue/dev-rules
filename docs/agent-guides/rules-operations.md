# 规则安装与同步运维

仅在接入项目、修复同步/加载位、安装 hooks/launcher 或排查云端环境时读取对应章节。路径与命令相对消费项目根目录；在 dev-rules 源仓库去掉 `dev-rules/` 前缀。

## 项目结构

每个 IT 产品研发项目必须包含 `dev-rules` 作为 git submodule。仓库入口索引见 `dev-rules/README.md`；接入、同步与门禁细节以本文件为准。

```
项目根目录/
├── dev-rules/                       ← git submodule
├── .cursor/rules/*.mdc              ← sync 产物（禁止直接编辑）
├── AGENTS.md                        ← Codex + Antigravity 共用工作区规则；dev-rules:codex 受管块由 sync 生成（禁止手编块内）
├── .codex/skills                    → ../.cursor/skills（Codex 项目级技能 symlink；项目 .gitignore 自定是否 track）
├── .agents/skills                   → ../.cursor/skills（Antigravity 工作区技能 symlink；同上）
├── scripts/preflight.sh             ← 可选项目 wrapper
└── .git/hooks/{pre-commit,commit-msg} ← install-hooks.sh 安装；pre-commit 按
                                        scripts/preflight.sh
                                        → dev-rules/templates/preflight.sh fallback；
                                        commit-msg 直跑高风险审批锚点检查——唯一同时
                                        知道 staged paths + 待提交 message 的本地阶段，
                                        token 缺失在此硬拦截

家目录消费端（由 `sync.sh` 维护；除 additive registry 外为 symlink）：
~/.cursor/rules/      → dev-rules/rules/
~/.cursor/skills      ← additive registry（仅 dev-rules-owned skill links 指向配置的 agent-skills；保留其它 owner 条目）
~/.claude/commands/   → dev-rules/commands/
~/.claude/CLAUDE.md   → dev-rules/global/CLAUDE.md
~/.claude/skills      → ~/.cursor/skills
~/.local/bin/<name>   → dev-rules/global/bin/<name>（CLI launcher；secret 留本机 ~/.claude/*.json 不入库）
~/.codex/AGENTS.md    → dev-rules/global/CLAUDE.md（Codex 与 Claude 同一宪法）
~/.codex/skills/<name>→ 配置的 agent-skills/<name>（逐个叠加，只维护 dev-rules-owned links，不动 Codex 自带 .system/default.rules）
~/.gemini/antigravity-cli/AGENTS.md    → dev-rules/global/CLAUDE.md（Antigravity 与 Claude/Codex 同一宪法）
~/.gemini/antigravity-cli/skills/<name>→ 配置的 agent-skills/<name>（逐个叠加，只维护 dev-rules-owned links，不动 Antigravity 自带 builtin）
~/.gemini/skills/<name>                → 配置的 agent-skills/<name>（逐个叠加，Gemini CLI 全局技能）
```

Codex 消费端要点：Codex 不读 `.cursor/rules/*.mdc`，行为规则经 `AGENTS.md` 的 dev-rules 受管块**指针化**到达（宪法 + 规则索引 + 技能索引 + 全局工具）；`~/.codex/rules/` 是 Codex 自己的命令审批策略（Starlark `.rules`），**dev-rules 永不写入**；三端通用 skill 都在 `.cursor/skills/` 保持同一份 `SKILL.md`，由各自的 skill 加载位消费。Codex 自定义 prompt 已被 OpenAI deprecated，可复用工作流一律走 skill，不 symlink commands 到 `~/.codex/prompts/`。

Antigravity CLI（Google `agy`）消费端要点：customization 模型与 Codex 同构，故复用同一管线。家目录 Global Customizations Root = `~/.gemini/antigravity-cli/`：全局规则读 `AGENTS.md`（symlink 同一宪法），技能自动发现 `skills/<name>/SKILL.md`（逐个 symlink）。工作区 Workspace Root = `<project>/.agents/`：技能读 `.agents/skills/<name>/`，**工作区规则复用项目根 `AGENTS.md` 的同一受管块**（与 Codex 同文件，零额外生成）。Antigravity 同样不自动加载 `.cursor/rules/*.mdc`，规则经 AGENTS.md 到达；不碰 `~/.gemini/GEMINI.md`（那是 gemini-cli 的根，非 antigravity-cli root）；可复用工作流走 skill，不 symlink commands。

Gemini CLI（Google `gemini`）消费端要点：全局技能由 `sync.sh` 逐个同步到 `~/.gemini/skills/<name>`；工作区技能直接消费 `<project>/.agents/skills`（与 Antigravity 共用同一加载位，新 worktree 由 `wts` 自动确保 symlink 就绪）。

## 新项目接入

```bash
cd 项目根目录
DEV_RULES_REMOTE_URL="${DEV_RULES_REMOTE_URL:?set DEV_RULES_REMOTE_URL}"
git submodule add "$DEV_RULES_REMOTE_URL" dev-rules
dev-rules/sync.sh --local                   # 复制规则 + 自动 register 该项目
git add .cursor/rules/ .gitmodules dev-rules
git commit -m "chore: add dev-rules submodule and sync rules"
```

**首次在本机使用**（仅一次）：

```bash
DEV_RULES_REMOTE_URL="${DEV_RULES_REMOTE_URL:?set DEV_RULES_REMOTE_URL}"
git clone "$DEV_RULES_REMOTE_URL" ~/Codes/dev-rules
~/Codes/dev-rules/sync.sh                                          # 创建 home symlinks
bash ~/Codes/dev-rules/templates/install-launchagent.sh            # 注册跨机器同步 agent
```

之后在任意项目里 `sync.sh --local` 就会自动把项目 `(name, git remote URL)` 写进 `~/Codes/dev-rules/.registered-projects`（git-tracked，跨机器共享），并把 `(git URL, 本机绝对路径)` 写进 `.local-projects`（per-machine，gitignored）。下一次 `--push` / `--pull` / LaunchAgent 会按本机实际落地的项目自动 fan-out。

fan-out 使用 `.registered-projects` 与 `.local-projects` 的并集：跨机器登记项目在本机有路径会同步；只存在于 `.local-projects` 的本机项目也会同步。后者适合 zw-brain 这类不希望在项目仓库暴露 dev-rules 远端 URL、但仍希望从 `~/Codes/dev-rules` 获得规则副本的项目。

换机器时，跨机器项目重新 `--local` 一次即可重建本机映射；本机-only 项目需要在该机器写入 `.local-projects` 或手动执行 `sync.sh --project /path/to/project`。

## Skill 加载位

**skill 加载位的单一事实来源**：skill 只在项目的 `.cursor/skills/` 编辑与提交（Cursor 读 `.cursor`，Claude Code 读 `.claude`，Codex 读 `~/.codex/skills` 与项目 `.codex/skills`，Antigravity 读 `~/.gemini/antigravity-cli/skills` 与项目 `.agents/skills`）。让其它端加载同一份 skill **只能靠 symlink，禁止真实副本**（副本会分叉事实来源）。`sync.sh` 确定性维护这些 links：家目录 `~/.cursor/skills` 是 additive consumer registry，不是全目录 source owner；它是真实目录，只创建和清理指向配置 `agent-skills` checkout 的 dev-rules-owned `<name>` links，保留 foreign symlink、real file 与 real directory，并在同名 foreign collision 时 fail closed。`~/.claude/skills → ~/.cursor/skills` 仍是唯一 whole-directory link。Codex 与 Antigravity 都逐个直接 link 配置的 `agent-skills/<name>`，不扫描或接管混合 registry 中的其它 owner 条目；Codex 不动 `.system` / `codex-primary-runtime` / `default.rules`，Antigravity 不动 builtin。项目 fan-out 时，若项目存在 `.cursor/skills/` 则建 `<project>/.claude/skills`、`<project>/.codex/skills`、`<project>/.agents/skills` 三者均 `→ ../.cursor/skills`。`sync.sh --check` 只守卫家目录 registry 中派生自配置 source 的 dev-rules links，另守卫 Claude whole-directory link 与 Codex/Antigravity owned links；foreign registry entries 不算漂移。手动建该 symlink 属安装期 debt，应记入项目 `docs/preflight-debt.md`。

**Codex 加载上限**：Codex 0.122 拒绝 `description` 超过 1024 字符的 `SKILL.md`（静默丢弃该技能，Cursor/Claude 无此限）。为 Codex 复用，`dev-rules/scripts/check_codex_skill_limits.py` 在 preflight 把超限技能列为 exit 1，迫使在 `.cursor/skills/` 源头收敛描述长度，而非让 Codex 端静默缺技能。

### 标准链路

```bash
# 1. 在任意项目内编辑 dev-rules/{rules,commands,global}/* 后：
dev-rules/sync.sh --local          # 同步到本项目 .cursor/rules/ + 自动 register
cd dev-rules
git add -A && git commit -m "update rules"
./sync.sh --push                   # = git push + ~/Codes mirror pull + 所有项目 fan-out
cd ..
git add dev-rules .cursor/rules/
git commit -m "chore: sync dev-rules" && git push
```

`sync.sh --push` 是推荐的一步入口；断网或 debug 时可拆为 `git push` + `cd ~/Codes/dev-rules && git pull --ff-only && ./sync.sh --all`，效果完全等价。

### 跨机器同步（被动）

LaunchAgent `local.dev-rules.sync` 每 30 分钟跑 `sync.sh --pull`，把其他机器推上来的 `origin/main` 更新拉到 canonical `main` checkout 并 fan-out 到本机已落地的注册项目。`--pull` 在 canonical checkout 不是 `main`（含 detached HEAD）时必须在 fan-out 前 fail-closed，禁止从 feature branch 分发规则。若 main 使用独立 worktree，安装 LaunchAgent 时通过 `DEV_RULES_HOME=/absolute/main/worktree` 固定脚本路径与 canonical source；如果 `verify-rules.sh` 报告 LaunchAgent 未装，跑 `templates/install-launchagent.sh`。

## 强约束门禁（机械检查，禁止仅"靠自觉"）

每条软规则都有对应的可执行脚本。**完整的「软规则 → 硬检查」映射表是单一事实来源**，定义在 `dev-rules/digital-clone-research.md §二`。本节只列接入步骤：

1. **项目级 wrapper（可选）**：仅当项目有「dev-rules 模板未覆盖的特异检查」时，`cp dev-rules/templates/preflight.sh scripts/preflight.sh` 并在 wrapper 里追加项目段。否则**不需要**新建 `scripts/preflight.sh`——`install-hooks.sh` 会自动 fallback 到 `dev-rules/templates/preflight.sh`。
2. **安装 git hook**：`bash dev-rules/templates/install-hooks.sh`。Hook 在运行时按以下顺序解析 preflight：
   ```
   $REPO_ROOT/scripts/preflight.sh           ← 项目 wrapper（若存在）
   $REPO_ROOT/dev-rules/templates/preflight.sh   ← 模板（fallback）
   ```
   增删项目 wrapper 后**无需**重装 hook。
3. **CI 同步运行**：在 GitHub Actions / GitLab CI 中追加一步 `./scripts/preflight.sh`（若存在）或 `./dev-rules/templates/preflight.sh`。
4. **dev-rules 子模块自身**：源仓库内置 `scripts/preflight.sh`（调用 `./verify-rules.sh` + `./sync-stats.sh --check`），首次克隆后跑一次 `bash templates/install-hooks.sh` 即可让 pre-commit 自动拦截违例提交。**不要靠记忆手动跑 verify-rules.sh**——这条规则正是「确定性自动化运营和运维」「自动化优先」要消灭的"自觉履约"。

接入完成后，违反任何一条强约束的提交都会被 hook 拦截，违反者的 CI 会 fail。

如果某项检查段在该项目尚不可自动化（例如还没有 API 时跳过 contract drift），必须把缺口记入 `docs/preflight-debt.md` 并设定截止日期，**禁止悄悄降级为"靠自觉"**。

## 全局 hooks 与 launcher

Claude Code 全局 hooks（如 `gh-pr-guard.py`、`skill-reflect.sh`）唯一源在 `dev-rules/global/hooks/`，由 `sync.sh` symlink 到 `~/.claude/hooks/`；`~/.claude/hooks/` 下不得有真实文件——发现非 symlink 必须 mv 到 canonical mirror 后重 sync。settings.json hook 条目按 `~/.claude/hooks/<name>` 路径引用即可（symlink 透明）。

CLI launcher（如 `claude-kiro`、`claude-doubao`：换 token / 后端启动 claude；`wts`：建 submodule-safe worktree 并进入 Cursor / Codex / Claude / Gemini / Kimi / DeepSeek Harness 交互会话）唯一源在 `dev-rules/global/bin/`，由 `sync.sh` symlink 到 `~/.local/bin/`，同样禁止真实副本。token 类 launcher 一律用 `claude --settings <本地文件>` 注入差异配置（settings.json 的 `env` 块会覆盖 shell export，export 模式会静默失效）；secret 只存本机 `~/.claude/*.json`，永不入库。可选 shell 包装见 `global/lib/wts.sh`。

## 云端 Agent / 本地 Agent 运行环境一致性

云端 Cursor Agent 跑在临时 VM，本地 Cursor Agent 跑在你的 dev 机器；两边的 `claude` CLI、`gh`、`jq`、相关 secrets 必须保持一致，否则同一段 prompt 在两个环境产生不同行为。

**单一事实来源**：`dev-rules/templates/cloud-agent-bootstrap.sh` 同时承担「云端首次安装」与「本地一致性自检」。项目只需要在 `.cursor/cloud-agent.env` 里声明工具与 secrets 契约（模板见 `dev-rules/templates/cloud-agent.env.example`）。

**典型用法**：

1. 项目根目录创建 `.cursor/cloud-agent.env`（按 `.example` 复制裁剪），声明：
   - `CLOUD_AGENT_TOOLS`（claude / gh / jq 自动安装；其他工具仅 PATH 检查）
   - `CLOUD_AGENT_REQUIRED_SECRETS`（缺失即 fail，例如 `ANTHROPIC_API_KEY` 或自建网关用 `ANTHROPIC_AUTH_TOKEN`）
   - `CLOUD_AGENT_OPTIONAL_SECRETS`（缺失只 warn，例如 `GH_TOKEN`）
   - `CLOUD_AGENT_CLAUDE_BASE_URL`（用自建网关时设置；留空走 Anthropic SaaS）
   - `CLOUD_AGENT_PROJECT_HOOK`（项目特异步骤，比如 submodule 同步、frontend deps）
2. 云端入口 `.cursor/environment.json` 指向 `bash dev-rules/templates/cloud-agent-bootstrap.sh`（默认 install 模式）。
3. 本地任何时刻可跑 `bash dev-rules/templates/cloud-agent-bootstrap.sh --check`；preflight 的 cloud-agent consistency 检查也会自动调用，secrets 未配置 / 工具缺失会被机械拦截。
4. Cursor Dashboard → Cloud Agents → Secrets 里配置 `CLOUD_AGENT_REQUIRED_SECRETS` 列出的所有变量，`--check` 即归零。

具体调用：`claude -p "..." --max-budget-usd N`。
