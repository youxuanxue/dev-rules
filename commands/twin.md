运行 xuejiao 分身监督 code agent：

$ARGUMENTS

## 定位

`/user:twin` 是本机 xuejiao supervisor harness 的入口。它从 Cursor / Claude Code 历史中生成 redacted 派生画像，并用两个隔离的 Claude Code headless session 监督真实项目任务：

- supervisor session：模拟 xuejiao，只生成指令、检查证据、判断继续 / 停止 / 升级。
- worker session：执行代码修改、测试和验证。

二者不得共用同一个 Claude Code session。supervisor 默认不直接改项目代码。

## 子命令

```text
/user:twin index [--since 180d] [--project /abs/path] [--out .xuejiao-twin/index.json]
/user:twin derive --index .xuejiao-twin/index.json --out .xuejiao-twin/persona.json
/user:twin init --goal-file goal.yaml --persona .xuejiao-twin/persona.json
/user:twin run --project /abs/path --mode supervised-normal
/user:twin validate [--fixtures | .xuejiao-twin/runs/<run_id>]
/user:twin replay .xuejiao-twin/runs/<run_id>/run.json
```

等价 CLI：

```bash
python3 -m scripts.xuejiao_twin <subcommand> ...
```

## 默认安全边界

- 历史语料只输出 redacted 派生产物，不保存未脱敏原文。
- Cursor chat SQLite 首版只读取 metadata，不解码 BLOB 正文。
- 默认输出写入目标项目 gitignored `.xuejiao-twin/`。
- `run` 首版支持 `dry-run` 与 supervised 模式，不自动 merge / push / deploy / 创建 PR。
- 遇到架构、安全、数据、依赖、外部副作用或破坏性操作，必须停止并报告 `needs_human`。

## 推荐流程

1. 生成画像：

```bash
python3 -m scripts.xuejiao_twin index --out ~/.xuejiao-twin/index.json
python3 -m scripts.xuejiao_twin derive --index ~/.xuejiao-twin/index.json --out ~/.xuejiao-twin/persona.json
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
