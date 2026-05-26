#!/usr/bin/env python3
"""Claude Code hook: enforce two rules around `gh pr` operations.

PreToolUse (`pre-merge` subcommand):
  Block `gh pr merge` invocations. Merge requires explicit user authorization
  in conversation, not auto-firing after PR-create or as a follow-up tool call.
  exit 2 + stderr blocks the tool call.

PostToolUse (`post-pr-create` subcommand):
  After `gh pr create` runs, inject a `decision: block` reason that pushes
  the assistant into the /xj-review loop with strict merge-ready criteria.

Key correctness property: we MUST distinguish a real command-leading
`gh pr merge` from an incidental literal substring (`echo "gh pr merge"`,
`grep "gh pr merge"`, etc). We do this by:

  1. Splitting the command on shell separators: && || ; |
  2. Stripping leading env-var prefixes (NAME=value NAME2=value2 ...)
  3. Checking the remaining head of each segment starts with the target.

This catches:
  - `gh pr merge 48 --auto`
  - `cd /path && gh pr merge`
  - `ENV=x gh pr merge`

And ignores:
  - `echo "gh pr merge"`
  - `grep 'gh pr merge' README.md`
  - `git log | grep merge`
"""
from __future__ import annotations

import json
import re
import sys


_QUOTED_SINGLE = re.compile(r"'[^']*'")
_QUOTED_DOUBLE = re.compile(r'"[^"]*"')


def _strip_quoted(cmd: str) -> str:
    """Replace quoted regions with empty placeholders so shell-separator splits
    don't fire inside literal strings (e.g. `echo 'gh pr merge'`).

    Note: does not handle nested or escaped quotes. Threat model is accidental
    auto-merge by the assistant, not adversarial shell crafting.
    """
    cmd = _QUOTED_SINGLE.sub("''", cmd)
    cmd = _QUOTED_DOUBLE.sub('""', cmd)
    return cmd


def command_leading_matches(cmd: str, head_pattern: str) -> bool:
    """True if any shell sub-segment starts with the given regex (after env-var prefix)."""
    if not cmd:
        return False
    cmd = _strip_quoted(cmd)
    segments = re.split(r"\s*(?:&&|\|\||;|\|)\s*", cmd)
    head_re = re.compile(head_pattern)
    env_prefix_re = re.compile(r"^(?:[A-Z_][A-Z0-9_]*=\S+\s+)+")
    for s in segments:
        s = s.strip()
        s = env_prefix_re.sub("", s)
        if head_re.match(s):
            return True
    return False


def load_input() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {}


def handle_pre_merge(cmd: str) -> int:
    if command_leading_matches(cmd, r"^gh\s+pr\s+merge\b"):
        sys.stderr.write(
            "禁止无授权 merge：本次会话尚未收到明确合并指令(如 \"合并 #XX\")。"
            "请先与用户确认；merge 不在 /xj-review 自动范围内。\n"
        )
        return 2
    return 0


def handle_post_pr_create(cmd: str) -> int:
    if command_leading_matches(cmd, r"^gh\s+pr\s+create\b"):
        payload = {
            "decision": "block",
            "reason": (
                "PR 已创建。下一步必做：调用 /xj-review 对本 PR 审查，按严格 "
                "merge-ready 准则（零 medium+ finding，包含 out-of-scope 顺手问题 "
                "与 Jobs / 确定性自动化运营和运维原则违背）循环 fix → re-review。达到 merge-ready "
                "后，**停下并在对话中等待用户的明确合并指令**；禁止直接调用 "
                "gh pr merge。"
            ),
        }
        print(json.dumps(payload, ensure_ascii=False))
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: gh-pr-guard.py <pre-merge|post-pr-create>\n")
        return 2
    mode = sys.argv[1]
    data = load_input()
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if mode == "pre-merge":
        return handle_pre_merge(cmd)
    if mode == "post-pr-create":
        return handle_post_pr_create(cmd)
    sys.stderr.write(f"unknown hook mode: {mode}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
