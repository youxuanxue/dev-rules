#!/usr/bin/env python3
"""Flag Agent Skill descriptions that Codex CLI silently refuses to load.

Codex 0.122 rejects a skill whose SKILL.md `description` exceeds 1024 chars:

    ERROR codex_core::session: failed to load skill .../SKILL.md:
    invalid description: exceeds maximum length of 1024 characters

Codex also reports `missing YAML frontmatter delimited by ---` when the file
does not begin with `---` (common causes: UTF-8 BOM, leading whitespace, or an
agent saving SKILL.md before the frontmatter block is written). This check flags
those load failures before Codex silently drops the skill.

The skill is then dropped from Codex entirely with no prompt — the operator
only finds out by reading stderr. Cursor and Claude Code have no such cap, so a
description authored for them silently breaks Codex reuse. This check makes that
failure a visible gate. It does NOT rewrite descriptions (that's authoring work
in the skill repo) — it reports which skills exceed the limit so they get fixed.

Scope: every `.cursor/skills/*/SKILL.md` reachable from cwd (the symlink to
agent-skills is followed transparently). Self-skips when no skills are found.

Usage:
  check_codex_skill_limits.py                 # scan ./.cursor/skills
  check_codex_skill_limits.py --root PATH     # scan PATH/.cursor/skills or PATH/*/SKILL.md
  check_codex_skill_limits.py --self-test
"""
from __future__ import annotations

import argparse
import pathlib
import sys

try:
    from preflight_common import cli_fail
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from preflight_common import cli_fail

# Reuse the exact frontmatter parser the generator uses (folded-scalar aware),
# so "what we measure" matches "what Codex reads".
try:
    from gen_codex_agents import FRONTMATTER_RE, parse_frontmatter
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from gen_codex_agents import FRONTMATTER_RE, parse_frontmatter

PREFIX = "check_codex_skill_limits"
# Observed limit in Codex 0.122 (the runtime error message). Not in public docs;
# treated as authoritative because Codex itself enforces it at load time.
DESCRIPTION_MAX = 1024


def discover(root: pathlib.Path) -> list[pathlib.Path]:
    skills_dir = root / ".cursor" / "skills"
    if skills_dir.is_dir():
        return sorted(skills_dir.glob("*/SKILL.md"))
    # Allow pointing --root straight at a skills collection (e.g. agent-skills).
    return sorted(root.glob("*/SKILL.md"))


def overlong(files: list[pathlib.Path]) -> list[tuple[pathlib.Path, int]]:
    out: list[tuple[pathlib.Path, int]] = []
    for f in files:
        fm = parse_frontmatter(f.read_text(encoding="utf-8"))
        desc = fm.get("description", "")
        if len(desc) > DESCRIPTION_MAX:
            out.append((f, len(desc)))
    return out


def invalid_frontmatter(files: list[pathlib.Path]) -> list[str]:
    """Return human-readable issues that make Codex skip a skill."""
    issues: list[str] = []
    for f in files:
        raw = f.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            issues.append(f"{f}: UTF-8 BOM before opening --- (Codex reports missing frontmatter)")
            continue
        text = raw.decode("utf-8")
        if not FRONTMATTER_RE.match(text):
            issues.append(f"{f}: missing YAML frontmatter delimited by --- at byte 0")
            continue
        fm = parse_frontmatter(text)
        name = fm.get("name", "").strip()
        desc = fm.get("description", "").strip()
        if not name or not desc:
            issues.append(f"{f}: frontmatter must include non-empty name and description")
            continue
        if name != f.parent.name:
            issues.append(
                f"{f}: name {name!r} must match parent directory {f.parent.name!r} (agentskills.io)"
            )
    return issues


def run(root: pathlib.Path) -> int:
    files = discover(root)
    if not files:
        print(f"[{PREFIX}] skip: no .cursor/skills/*/SKILL.md under {root}")
        return 0
    fm_issues = invalid_frontmatter(files)
    if fm_issues:
        return cli_fail(PREFIX, "skill frontmatter invalid for Codex", *fm_issues)
    bad = overlong(files)
    if bad:
        details = [
            f"{f.parent.name}: description {n} chars (> {DESCRIPTION_MAX}; Codex will drop this skill)"
            for f, n in bad
        ]
        return cli_fail(PREFIX, "skill description(s) exceed Codex limit", *details)
    print(
        f"[{PREFIX}] {len(files)} skill description(s) within Codex {DESCRIPTION_MAX}-char limit "
        "and frontmatter OK"
    )
    return 0


def _self_test() -> int:
    import tempfile

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        skills = root / ".cursor" / "skills"
        (skills / "ok").mkdir(parents=True)
        (skills / "toolong").mkdir(parents=True)
        (skills / "ok" / "SKILL.md").write_text(
            "---\nname: ok\ndescription: short and fine\n---\nbody\n", encoding="utf-8"
        )
        long_desc = "x" * (DESCRIPTION_MAX + 5)
        (skills / "toolong" / "SKILL.md").write_text(
            f"---\nname: toolong\ndescription: {long_desc}\n---\nbody\n", encoding="utf-8"
        )
        bad = overlong(discover(root))
        names = {f.parent.name for f, _ in bad}
        if names != {"toolong"}:
            failures.append(f"expected only 'toolong' flagged, got {names}")
        if run(root) == 0:
            failures.append("run() should fail when a description is over the limit")

        # All-clean tree passes.
        (skills / "toolong" / "SKILL.md").write_text(
            "---\nname: toolong\ndescription: now short\n---\nbody\n", encoding="utf-8"
        )
        if run(root) != 0:
            failures.append("run() should pass when all descriptions are within limit")

        (skills / "bombad").mkdir(parents=True)
        (skills / "bombad" / "SKILL.md").write_bytes(
            b"\xef\xbb\xbf---\nname: bombad\ndescription: has bom\n---\nbody\n"
        )
        if run(root) == 0:
            failures.append("run() should fail when a skill has UTF-8 BOM")

    if failures:
        return cli_fail(PREFIX, "self-test failed", *failures)
    print(f"[{PREFIX}] self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root (or a skills collection)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return _self_test()
    return run(pathlib.Path(args.root).resolve())


if __name__ == "__main__":
    sys.exit(main())
