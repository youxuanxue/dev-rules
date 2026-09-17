#!/usr/bin/env python3
"""Check opt-in instruction byte budgets and local Markdown reference/anchor links.

Limits are bytes, not token estimates. Each repository owns .agent-context-budget.json;
references are measured separately so moving detail does not masquerade as deleting it.
Run --json for repeatable measurement, --self-test for failure-path fixtures.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

LINK = re.compile(r"\[[^\]\n]*\]\(([^\s)]+)\)")
GUIDE = re.compile(r"`(dev-rules/docs/agent-guides/[^`\s]+\.md)`")


def prose(text: str) -> str:
    """Ignore code fences: examples may contain deliberately fictitious links."""
    lines = []
    fence = None
    width = 0
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match[1]
            if fence is None:
                fence, width = marker[0], len(marker)
            elif marker[0] == fence and len(marker) >= width:
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return "\n".join(lines)


def anchors(text: str) -> set[str]:
    found = set(re.findall(r'<a\s+(?:id|name)=[\"\']([^\"\']+)', text))
    counts: dict[str, int] = {}
    for line in prose(text).splitlines():
        heading = re.match(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", line)
        if not heading:
            continue
        label = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading[1])
        slug = re.sub(r"[^\w\s-]", "", label.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        found.add(slug + (f"-{count}" if count else ""))
    return found


def link_errors(root: Path, source: Path) -> list[str]:
    errors = []
    content = prose(source.read_text(encoding="utf-8"))
    for raw in LINK.findall(content):
        url = urlsplit(raw)
        if url.scheme or url.netloc:
            continue
        path = unquote(url.path)
        if path and Path(path).suffix not in {".md", ".mdc"}:
            continue
        target = (source.parent / path).resolve() if path else source
        if not target.is_file():
            errors.append(f"{source.relative_to(root)}: missing link {raw}")
        elif url.fragment and unquote(url.fragment) not in anchors(target.read_text(encoding="utf-8")):
            errors.append(f"{source.relative_to(root)}: missing anchor {raw}")
    # Rules are copied from dev-rules/rules to .cursor/rules. These explicit
    # root-relative guide routes must resolve in both source and consumer repos.
    for raw in GUIDE.findall(content):
        target = root / raw
        if not target.is_file():
            target = root / raw.removeprefix("dev-rules/")
        if not target.is_file():
            errors.append(f"{source.relative_to(root)}: missing guide {raw}")
    return errors


def inspect(root: Path, config: dict) -> dict:
    errors: list[str] = []
    groups = []
    sources: set[Path] = set()
    for group in config["groups"]:
        files: set[Path] = set()
        for pattern in group["patterns"]:
            matches = {p for p in root.glob(pattern) if p.is_file()}
            if not matches:
                errors.append(f"{group['name']}: pattern has no files: {pattern}")
            files.update(matches)
        rows = [{"path": str(p.relative_to(root)), "bytes": p.stat().st_size} for p in sorted(files)]
        total = sum(row["bytes"] for row in rows)
        for row in rows:
            if row["bytes"] > group["max_file_bytes"]:
                errors.append(f"{row['path']}: {row['bytes']} > file budget {group['max_file_bytes']}")
        if total > group["max_total_bytes"]:
            errors.append(f"{group['name']}: {total} > total budget {group['max_total_bytes']}")
        groups.append({"name": group["name"], "bytes": total, "files": rows})
        sources.update(files)
    references = {p for pattern in config.get("references", []) for p in root.glob(pattern) if p.is_file()}
    sources.update(references)
    for source in sorted(sources):
        errors.extend(link_errors(root, source))
    return {"groups": groups, "reference_bytes": sum(p.stat().st_size for p in references), "errors": errors}


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "refs").mkdir()
        source = root / "ENTRY.md"
        ref = root / "refs" / "detail.md"
        source.write_text("# Entry\n[detail](refs/detail.md#中文-owner)\n", encoding="utf-8")
        ref.write_text("# 中文 Owner\n", encoding="utf-8")
        config = {"groups": [{"name": "entry", "patterns": ["ENTRY.md"], "max_file_bytes": 100, "max_total_bytes": 100}], "references": ["refs/*.md"]}
        assert not inspect(root, config)["errors"]
        # UTF-8 bytes rather than character count: regressions cannot hide in CJK.
        source.write_text("字" * 40, encoding="utf-8")
        assert any("file budget" in e for e in inspect(root, config)["errors"])
        source.write_text("[lost](refs/lost.md)\n[stale](refs/detail.md#stale)\n", encoding="utf-8")
        errors = inspect(root, config)["errors"]
        assert any("missing link" in e for e in errors)
        assert any("missing anchor" in e for e in errors)
        source.write_text("```md\n[fake](no.md)\n```\n[ok](refs/detail.md#中文-owner)\n", encoding="utf-8")
        assert not inspect(root, config)["errors"]
        source.unlink()
        assert any("no files" in e for e in inspect(root, config)["errors"])
        assert "same-1" in anchors("# Same\n# Same\n")
        assert not anchors("````\n```\n# Hidden\n````\n")
    print("[check_agent_context] self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    root = args.root.resolve()
    try:
        config = json.loads((root / ".agent-context-budget.json").read_text(encoding="utf-8"))
        result = inspect(root, config)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"[check_agent_context] invalid context budget: {exc}")
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("[check_agent_context] " + ", ".join(f"{g['name']}={g['bytes']}B" for g in result["groups"]) + f"; references={result['reference_bytes']}B")
        for error in result["errors"]:
            print(f"  FAIL: {error}")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
