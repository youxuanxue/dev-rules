# twin workspace template

共享 `twin` Skill 的最小可启动模板。复制到你的项目下，按真实目标改写 `goal.yaml` 与 `plan.yaml`，然后从当前宿主启动 supervisor。

```bash
cp -r dev-rules/templates/twin-workspace .twin/<slug>
$EDITOR .twin/<slug>/goal.yaml
$EDITOR .twin/<slug>/plan.yaml
```

Claude Code 使用 `/twin .twin/<slug>`，Codex 使用 `$twin .twin/<slug>`；Antigravity 调用 `twin` skill 并传入同一参数。直接给一行目标时也使用对应宿主入口；跨仓或方向不明时追加 `research`，已有研究材料时使用 `plan --research <research.yaml>`。

**别在 `dev-rules/templates/twin-workspace/` 原地跑或 validate**——它是模板，不是 workspace；`validate` 会在 workspace 里写出 `supervisor_state.json` 和 `runs/`，污染模板。复制走再改、再 validate、再从当前宿主调用 `twin` skill。

schema 字段定义见 `docs/twin-design.md`「workspace 契约」节；不要把 persona 文件复制进 workspace。运行中生成的 `CURRENT.md` 是人类状态面，`workspace_events.jsonl` 记录不含回答正文的人类门禁审计。
