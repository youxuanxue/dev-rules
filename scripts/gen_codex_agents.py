#!/usr/bin/env python3
"""Generate (or --check) the dev-rules managed block in a project's AGENTS.md.

Codex CLI reads <repo>/AGENTS.md as project instructions but ignores
.cursor/rules/*.mdc (a Cursor format) and has no behavioral-rules directory of
its own (~/.codex/rules is its command-approval policy, not coding rules). So
the way dev-rules capabilities reach Codex in a project is a deterministic,
idempotent managed block inside AGENTS.md that POINTS at:

  - the constitution (dev-rules/global/CLAUDE.md),
  - the behavioral rule set (.cursor/rules/*.mdc — name + one-line each),
  - the available skills (.cursor/skills/*/SKILL.md — name + description),
  - the /twin command (Claude-Code-only) and the cross-tool xj-review skill.

Everything inside the block is derived mechanically from on-disk artifacts —
no model inference — per rules/dev-rules-convention.mdc «skill/command 确定性基线».
The user's own prose lives OUTSIDE the BEGIN/END markers and is never touched.

Usage:
  gen_codex_agents.py --project /path/to/project        # write/update block
  gen_codex_agents.py --project /path/to/project --check # CI: exit 1 on drift
  gen_codex_agents.py --self-test                        # internal invariants
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import tempfile

try:
    from preflight_common import cli_fail
except ImportError:  # pragma: no cover - allow running from any cwd
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from preflight_common import cli_fail

PREFIX = "gen_codex_agents"

BEGIN = "<!-- dev-rules:codex BEGIN — generated, do not edit by hand -->"
END = "<!-- dev-rules:codex END -->"
# Match the whole managed block (markers included), tolerant of surrounding
# blank lines so re-runs don't accumulate them.
BLOCK_RE = re.compile(
    r"\n*" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n*",
    re.DOTALL,
)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)
# First sentence-ish summary line of an .mdc rule's `description:` frontmatter.
ONELINE_MAX = 140


def _first_line(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse a leading `--- ... ---` block.

    Handles both `key: value` and YAML folded/literal scalars
    (`key: >-` / `key: |` followed by indented continuation lines), which is
    how agent-skills SKILL.md files write multi-line descriptions.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        # Only treat a line as a key when the colon is at the top indent level
        # (no leading whitespace) — indented lines belong to a folded scalar.
        if line[:1].isspace():
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value in (">", ">-", "|", "|-"):
            # Folded/literal scalar: gather following indented lines.
            collected: list[str] = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1].isspace()):
                collected.append(lines[i].strip())
                i += 1
            value = " ".join(c for c in collected if c).strip()
        out[key] = value
    return out


def _oneline(value: str) -> str:
    one = " ".join(value.split())
    if len(one) > ONELINE_MAX:
        one = one[: ONELINE_MAX - 1].rstrip() + "…"
    return one


def collect_rules(project: pathlib.Path) -> list[tuple[str, str]]:
    """(stem, one-line description) for each .cursor/rules/*.mdc, sorted."""
    rules_dir = project / ".cursor" / "rules"
    out: list[tuple[str, str]] = []
    if not rules_dir.is_dir():
        return out
    for mdc in sorted(rules_dir.glob("*.mdc")):
        fm = parse_frontmatter(mdc.read_text(encoding="utf-8"))
        out.append((mdc.stem, _oneline(fm.get("description", ""))))
    return out


def collect_skills(project: pathlib.Path) -> list[tuple[str, str]]:
    """(name, one-line description) for each .cursor/skills/*/SKILL.md, sorted.

    Follows the .cursor/skills symlink (agent-skills) transparently. `name`
    comes from frontmatter when present, else the directory name.
    """
    skills_dir = project / ".cursor" / "skills"
    out: list[tuple[str, str]] = []
    if not skills_dir.is_dir():
        return out
    for child in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if child.name.startswith("."):
            continue
        manifest = child / "SKILL.md"
        if not manifest.is_file():
            continue
        fm = parse_frontmatter(manifest.read_text(encoding="utf-8"))
        name = fm.get("name", "").strip() or child.name
        out.append((name, _oneline(fm.get("description", ""))))
    return out


def has_dev_rules_submodule(project: pathlib.Path) -> bool:
    return (project / "dev-rules" / "global" / "CLAUDE.md").is_file()


def render_block(project: pathlib.Path) -> str:
    """Render the managed block body from on-disk artifacts (deterministic)."""
    rules = collect_rules(project)
    skills = collect_skills(project)
    constitution = (
        "dev-rules/global/CLAUDE.md"
        if has_dev_rules_submodule(project)
        else "~/.codex/AGENTS.md (global constitution symlink)"
    )
    # The generator path must be consumer-relative: in a project that vendors
    # dev-rules as a submodule the script lives at dev-rules/scripts/..., not
    # scripts/.... Emitting the bare scripts/ path makes the consumer's own
    # script-ref existence checker (scripts/checks/script-ref-existence.py) flag
    # a stale reference, because it resolves the literal against the project root.
    # Mirror the constitution path's submodule-awareness above.
    gen_script_ref = (
        "dev-rules/scripts/gen_codex_agents.py"
        if has_dev_rules_submodule(project)
        else "scripts/gen_codex_agents.py"
    )

    lines: list[str] = [BEGIN, ""]
    lines.append(
        f"本节由 `dev-rules/sync.sh` 经 `{gen_script_ref}` 确定性生成；"
        "请勿手工编辑标记之间的内容（手写说明放到标记之外）。"
    )
    lines.append("")
    lines.append("## 工作宪法（单一事实来源）")
    lines.append(
        f"- 会话级硬纪律与身份：见 [`{constitution}`]({constitution})。"
        "Codex 与 Claude Code、Cursor 共用同一份宪法。"
    )
    lines.append("")

    lines.append("## 行为规则（按需展开阅读）")
    if rules:
        lines.append(
            "Codex 不自动加载 `.cursor/rules/*.mdc`；需要时按下表路径读取对应文件："
        )
        lines.append("")
        for stem, desc in rules:
            path = f".cursor/rules/{stem}.mdc"
            suffix = f" — {desc}" if desc else ""
            lines.append(f"- [`{path}`]({path}){suffix}")
    else:
        lines.append("- （本项目 `.cursor/rules/` 暂无规则文件）")
    lines.append("")

    lines.append("## 可用技能（progressive disclosure）")
    if skills:
        lines.append(
            "以下技能源在 `.cursor/skills/`（与 `~/.cursor/skills` 同源）；"
            "Codex 也可经 `~/.codex/skills/<name>` 原生加载。需要时读对应 `SKILL.md`："
        )
        lines.append("")
        for name, desc in skills:
            suffix = f" — {desc}" if desc else ""
            lines.append(f"- **{name}**{suffix}")
    else:
        lines.append("- （本项目 `.cursor/skills/` 暂无技能）")
    lines.append("")

    lines.append("## 命令")
    lines.append(
        "- `/twin <workspace>|status [workspace]|respond <text>` — 运行 xuejiao persona "
        "supervisor 驱动 worker；底层入口 `python3 -m scripts.twin`（见 "
        "`dev-rules/commands/twin.md`）。Claude-Code-only。"
    )
    lines.append(
        "- 代码审查走三端通用 skill `xj-review`（上面技能索引里）：先跑 `preflight.sh` 取 "
        "ground-truth，再按风险分级审；Codex 里描述\"review 这个 diff/PR\"即触发。"
    )
    lines.append("")
    lines.append(END)
    return "\n".join(lines)


def compose(existing: str, block: str) -> str:
    """Insert/replace the managed block in `existing` AGENTS.md text.

    - Replaces an existing block in place (idempotent).
    - Otherwise appends after the user's content, separated by one blank line.
    """
    if BLOCK_RE.search(existing):
        # Function replacement so `block` is inserted LITERALLY — a skill/rule
        # description may contain backslash sequences (\1, \g<0>, C:\path) that
        # re.sub would otherwise interpret as group references and crash.
        replaced = BLOCK_RE.sub(lambda _m: "\n\n" + block + "\n", existing)
        return replaced.lstrip("\n").rstrip() + "\n"
    body = existing.rstrip()
    if body:
        return body + "\n\n" + block + "\n"
    return block + "\n"


def desired_text(project: pathlib.Path) -> str:
    agents = project / "AGENTS.md"
    existing = agents.read_text(encoding="utf-8") if agents.is_file() else ""
    return compose(existing, render_block(project))


def run(project: pathlib.Path, *, check: bool) -> int:
    if not project.is_dir():
        return cli_fail(PREFIX, f"project not found: {project}")
    agents = project / "AGENTS.md"
    want = desired_text(project)
    have = agents.read_text(encoding="utf-8") if agents.is_file() else ""
    if want == have:
        print(f"[{PREFIX}] {agents}: managed block up to date")
        return 0
    if check:
        return cli_fail(
            PREFIX,
            f"{agents}: dev-rules managed block drifted",
            "run: dev-rules/sync.sh --project " + str(project),
        )
    agents.write_text(want, encoding="utf-8")
    print(f"[{PREFIX}] {agents}: managed block written")
    return 0


def _self_test() -> int:
    """Mechanical invariants: block render + idempotent compose + folded YAML."""
    failures: list[str] = []

    # 1. Folded-scalar frontmatter is parsed into a single-line value.
    fm = parse_frontmatter(
        "---\nname: foo\ndescription: >-\n  line one\n  line two\n---\nbody\n"
    )
    if fm.get("name") != "foo":
        failures.append(f"name parse: {fm!r}")
    if fm.get("description") != "line one line two":
        failures.append(f"folded scalar parse: {fm!r}")

    with tempfile.TemporaryDirectory() as tmp:
        proj = pathlib.Path(tmp)
        (proj / ".cursor" / "rules").mkdir(parents=True)
        (proj / ".cursor" / "skills" / "demo").mkdir(parents=True)
        (proj / ".cursor" / "rules" / "alpha.mdc").write_text(
            "---\ndescription: Alpha rule\nalwaysApply: true\n---\nbody\n",
            encoding="utf-8",
        )
        (proj / ".cursor" / "skills" / "demo" / "SKILL.md").write_text(
            "---\nname: demo\ndescription: A demo skill\n---\nbody\n",
            encoding="utf-8",
        )

        block = render_block(proj)
        if BEGIN not in block or END not in block:
            failures.append("markers missing from block")
        if "alpha.mdc" not in block or "Alpha rule" not in block:
            failures.append("rule index not rendered")
        if "**demo**" not in block or "A demo skill" not in block:
            failures.append("skill index not rendered")
        # generator self-reference path is consumer-relative. Without a vendored
        # dev-rules submodule the bare scripts/ path is correct.
        if "`scripts/gen_codex_agents.py`" not in block:
            failures.append("non-submodule gen path not rendered")

        # 2. compose into empty AGENTS.md, then re-compose → idempotent.
        once = compose("", block)
        twice = compose(once, render_block(proj))
        if once != twice:
            failures.append("compose not idempotent")
        if once.count(BEGIN) != 1 or once.count(END) != 1:
            failures.append("duplicate markers after compose")

        # 3. user prose outside markers is preserved across regeneration.
        user = "# My project\n\nHand-written note.\n"
        composed = compose(user, block)
        if "Hand-written note." not in composed:
            failures.append("user prose dropped")
        recomposed = compose(composed, render_block(proj))
        if "Hand-written note." not in recomposed or recomposed.count(BEGIN) != 1:
            failures.append("user prose not stable across regen")

        # 4. a skill description containing regex backref / backslash chars must
        # not crash the replace path (re.sub repl is literal via the lambda).
        (proj / ".cursor" / "skills" / "tricky").mkdir(parents=True)
        (proj / ".cursor" / "skills" / "tricky" / "SKILL.md").write_text(
            "---\nname: tricky\ndescription: uses \\1 and \\g<0> and path C:\\tmp\n---\nbody\n",
            encoding="utf-8",
        )
        tricky_block = render_block(proj)
        existing_block = BEGIN + "\nstale\n" + END
        try:
            out = compose(existing_block, tricky_block)
        except Exception as exc:  # noqa: BLE001 - any re error is a failure here
            failures.append(f"backref description crashed compose: {exc!r}")
        else:
            if out.count(BEGIN) != 1:
                failures.append("backref regen produced wrong marker count")

    # 5. when dev-rules is vendored as a submodule, the generator self-reference
    # must carry the dev-rules/ prefix so the consumer's script-ref existence
    # checker resolves it (separate temp dir to avoid mutating the project above).
    with tempfile.TemporaryDirectory() as tmp2:
        proj2 = pathlib.Path(tmp2)
        (proj2 / ".cursor" / "rules").mkdir(parents=True)
        (proj2 / ".cursor" / "skills").mkdir(parents=True)
        (proj2 / "dev-rules" / "global").mkdir(parents=True)
        (proj2 / "dev-rules" / "global" / "CLAUDE.md").write_text("c\n", encoding="utf-8")
        sub_block = render_block(proj2)
        if "`dev-rules/scripts/gen_codex_agents.py`" not in sub_block:
            failures.append("submodule gen path missing dev-rules/ prefix")
        if "经 `scripts/gen_codex_agents.py`" in sub_block:
            failures.append("submodule block still emits bare scripts/ gen path")

    if failures:
        return cli_fail(PREFIX, "self-test failed", *failures)
    print(f"[{PREFIX}] self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="project root containing AGENTS.md")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the managed block would change (CI/preflight)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run internal invariants and exit",
    )
    args = parser.parse_args()

    if args.self_test:
        return _self_test()
    if not args.project:
        return cli_fail(PREFIX, "--project is required (or use --self-test)")
    return run(pathlib.Path(args.project).resolve(), check=args.check)


if __name__ == "__main__":
    sys.exit(main())
