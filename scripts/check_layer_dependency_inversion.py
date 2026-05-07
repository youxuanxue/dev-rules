#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys

DEFAULT_CONFIG = pathlib.Path(".preflight/layer-deps.json")


def load_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def go_import_path(line: str) -> str | None:
    s = line.strip()
    if not s.startswith('"') or not s.endswith('"'):
        return None
    return s.strip('"')


def py_import_path(line: str) -> list[str]:
    s = line.strip()
    out: list[str] = []
    if s.startswith("import "):
        rest = s[len("import ") :]
        for part in rest.split(","):
            name = part.strip().split(" as ")[0].strip()
            if name:
                out.append(name)
    elif s.startswith("from ") and " import " in s:
        pkg = s[len("from ") : s.index(" import ")].strip()
        if pkg:
            out.append(pkg)
    return out


def js_import_path(line: str) -> str | None:
    s = line.strip()
    if " from " in s:
        quote = '"' if '"' in s else "'"
        if quote in s:
            try:
                return s.split(" from ", 1)[1].split(quote)[1]
            except IndexError:
                return None
    if s.startswith("import("):
        quote = '"' if '"' in s else "'"
        if quote in s:
            try:
                return s.split(quote)[1]
            except IndexError:
                return None
    return None


def iter_imports(path: pathlib.Path, lang: str) -> list[str]:
    imports: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return imports

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        if lang == "go":
            item = go_import_path(line)
            if item:
                imports.append(item)
        elif lang == "python":
            imports.extend(py_import_path(line))
        else:
            item = js_import_path(line)
            if item:
                imports.append(item)
    return imports


def match_any(value: str, patterns: list[str]) -> bool:
    return any(value.startswith(p) for p in patterns)


def check_rule(rule: dict) -> list[str]:
    errors: list[str] = []
    roots = [pathlib.Path(p) for p in rule.get("roots", [])]
    file_suffixes = tuple(rule.get("file_suffixes", []))
    lang = rule.get("language", "go")
    forbidden = rule.get("forbidden_import_prefixes", [])
    allow_self = rule.get("allow_within_root", True)

    for root in roots:
        if not root.exists():
            continue
        files = [
            p
            for p in root.rglob("*")
            if p.is_file() and (not file_suffixes or p.suffix in file_suffixes)
        ]
        for file in files:
            imports = iter_imports(file, lang)
            rel = file.as_posix()
            for imp in imports:
                if not match_any(imp, forbidden):
                    continue
                if allow_self and any(imp.startswith(r.as_posix()) for r in roots):
                    continue
                errors.append(f"{rel}: forbidden import '{imp}'")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check layered dependency inversion via project config."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg_path = pathlib.Path(args.config)
    if not cfg_path.is_file():
        print(f"[check_layer_dependency_inversion] skip: missing {cfg_path}")
        return 0

    cfg = load_json(cfg_path)
    rules = cfg.get("rules", [])
    if not isinstance(rules, list):
        sys.stderr.write("layer-deps config error: 'rules' must be a list\n")
        return 2

    violations: list[str] = []
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            sys.stderr.write(f"layer-deps config error: rule[{idx}] must be object\n")
            return 2
        violations.extend(check_rule(rule))

    if violations:
        sys.stderr.write("[check_layer_dependency_inversion] found forbidden dependencies\n")
        for v in violations:
            sys.stderr.write(f"  - {v}\n")
        return 1

    print("[check_layer_dependency_inversion] pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
