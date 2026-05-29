# preflight debt（dev-rules 自身）

记录「本应机械化、但暂未硬化进 `scripts/preflight.sh` / `verify-rules.sh`」的缺口。
每条必须写明：缺口、为何暂不脚本化、复发即硬化的触发条件。禁止悄悄降级为"靠自觉"。

## D-001 · diff 新增「零 consumer 面」未被机械检测

- **缺口**：PR 引入的新函数 / CLI 子命令 / 配置 flag / 路由，若全仓零引用（无人调用），目前只靠 `/xj-review` §103「多此一举 / 零调用函数必报」这条 *prompt 规则* 兜底，没有脚本强制。
- **复发证据**：2026-05-29，在为 `/xj-review` 抽 `scripts/review/loop_state.py` 时，作者（模型）揣着 §103 仍 ship 了零 consumer 的 `status` / `reset` 子命令，靠人工提问才发现并删除。这是「靠模型记得对自己也用一遍规则」= R-001 病在判断层的复发。
- **为何暂不脚本化（刻意 hold，不是遗忘）**：
  - `rules/dev-rules-convention.mdc §77`：过度机械化判断是另一种反模式。通用死代码检测天然多假阳——public API、入口点、动态派发 / 反射调用、跨仓被 consume 的导出，都会被误报。
  - memory `feedback_self_referential_review`：在 dev-rules 自身工具里扫描产物的 detector，假阳风险近乎必然，不是"低"。
  - 故首选**更锐的 prompt 规则 + review-time 兜底**，而非立刻造一个高假阳脚本。
- **复发即硬化的触发条件**：若「diff 新增零 consumer 面」再被人工逮到第 2 次，则按 `digital-clone-research.md §二` 落一个**窄检测**——仅作用于「本 PR diff 内新增的符号 / argparse 子命令」且「全仓 grep 零引用」，diff-scoped 以压住假阳；按 `check_*.py --self-test` 约定接入 `verify-rules.sh`。
