运行 xuejiao 分身监督 code agent：

$ARGUMENTS

## 定位

`/user:twin` 是本机 xuejiao supervisor harness 的入口。它读取由 Claude agent 维护的 `persona.json`，并用两个隔离的 Claude Code headless session 监督真实项目任务：

- supervisor session：模拟 xuejiao，只生成指令、检查证据、判断继续 / 停止 / 升级。
- worker session：执行代码修改、测试和验证。

二者不得共用同一个 Claude Code session。supervisor 默认不直接改项目代码。

## 子命令

```text
/user:twin init --goal-file goal.yaml --persona ~/.xuejiao-twin/persona.json
/user:twin run --project /abs/path --mode supervised-normal
/user:twin validate [--fixtures | .xuejiao-twin/runs/<run_id>]
/user:twin replay .xuejiao-twin/runs/<run_id>/run.json
```

等价 CLI：

```bash
python3 -m scripts.xuejiao_twin <subcommand> ...
```

## persona 维护边界

- `persona.json` 不由本命令自动生成。
- 更新 persona 时，由 Claude agent 直接读取本机 Cursor / Claude Code 历史，人工确认隐私边界后写入 `~/.xuejiao-twin/persona.json`。
- `persona.json` 不应包含项目名、仓库名、路径名、URL、token 或 secret。

## 默认安全边界

- 默认输出写入目标项目 gitignored `.xuejiao-twin/`。
- `dry-run` 只生成一轮 supervisor 预览；`supervised-*` 会在 `goal.limits` 内多轮运行直到完成、失败、无进展或需要人工。
- main / master 受保护，不在 main / master 上自动 commit 或 push。
- worker 若要接近 `bypassPermissions`，设置 `allowed_tools.worker: [Read, Edit, Write, Bash]`。
- runtime 会默认向 `--disallowedTools` 注入危险命令基线：force push、reset/clean/rm、infra apply/destroy、生产部署、publish、docker push、数据库 drop。
- `disallowed_tools.worker` 会追加传给 Claude Code `--disallowedTools`，用于按项目继续封堵危险命令。
- 非 main 工作分支可按 `goal.allowed_tools` 自动 commit、push、创建 PR 和本地部署验证。
- 遇到架构、安全、数据、依赖、生产发布、force push、外部副作用或破坏性操作，必须停止并报告 `needs_human`。

## 推荐流程

1. 准备 persona：

```text
~/.xuejiao-twin/persona.json
```

2. 在目标项目写 `goal.yaml`，包含 `project_root`、`goal`、`scope_in`、`scope_out`、`acceptance`、`limits`、`allowed_tools`、`approval_policy`、`validation_commands`。

3. 初始化：

```bash
python3 -m scripts.xuejiao_twin init --goal-file goal.yaml --persona ~/.xuejiao-twin/persona.json
```

4. 先 dry-run，再 supervised：

```bash
python3 -m scripts.xuejiao_twin run --project /abs/project --mode dry-run
python3 -m scripts.xuejiao_twin run --project /abs/project --mode supervised-normal
```

5. 回放和验证：

```bash
python3 -m scripts.xuejiao_twin replay .xuejiao-twin/runs/<run_id>/run.json
python3 -m scripts.xuejiao_twin validate .xuejiao-twin/runs/<run_id>
```

## 机械验证

本命令对应 fixture 自检：

```bash
python3 -m scripts.xuejiao_twin validate --fixtures
```
