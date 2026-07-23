# twin workspace template

`/twin <workspace>` 的最小可启动模板。复制到你的项目下，按真实目标改写 `goal.yaml` 与 `plan.yaml`，然后从该工作区启动 supervisor。

```bash
cp -r dev-rules/templates/twin-workspace .twin/<slug>
$EDITOR .twin/<slug>/goal.yaml
$EDITOR .twin/<slug>/plan.yaml
/twin .twin/<slug>
```

更推荐直接用 `/twin "<one-line goal>"` 让 supervisor 判断是否需要只读研究，再草拟 `goal.yaml + plan.yaml`，确认后执行。跨仓或方向不明时可显式使用 `/twin research`，再用 `/twin plan --research <research.yaml>`。

**别在 `dev-rules/templates/twin-workspace/` 原地跑或 validate**——它是模板，不是 workspace；`validate` 会在 workspace 里写出 `supervisor_state.json` 和 `runs/`，污染模板。复制走再改、再 validate、再 `/twin`。

schema 字段定义见 `docs/twin-design.md`「workspace 契约」节；不要把 persona 文件复制进 workspace。运行中生成的 `CURRENT.md` 是人类状态面，`workspace_events.jsonl` 记录不含回答正文的人类门禁审计。
