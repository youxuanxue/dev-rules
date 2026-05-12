# twin workspace template

`/twin <workspace>` 的最小可启动模板。复制到你的项目下，按真实目标改写 `goal.yaml` 与 `feature_ledger.yaml`，然后从该工作区启动 supervisor。

```bash
cp -r dev-rules/templates/twin-workspace .twin/<slug>
$EDITOR .twin/<slug>/goal.yaml
$EDITOR .twin/<slug>/feature_ledger.yaml
/twin .twin/<slug>
```

**别在 `dev-rules/templates/twin-workspace/` 原地跑或 validate**——它是模板，不是 workspace；`validate` 会在 workspace 里写出 `supervisor_state.json` 和 `runs/`，污染模板。复制走再改、再 validate、再 `/twin`。

schema 字段定义见 `docs/twin-design.md`「输入契约」节；不要把 persona 文件复制进 workspace。
