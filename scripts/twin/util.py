from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_yaml_like(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except Exception:
        return _read_simple_yaml(path)
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"expected mapping in {path}")
    return data


def write_yaml_like(path: Path, value: dict[str, Any]) -> None:
    try:
        import yaml  # type: ignore
    except Exception:
        path.write_text(_dump_simple_yaml(value), encoding="utf-8")
        return
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _line_indent(raw: str) -> int:
    return len(raw) - len(raw.lstrip(" "))


def _ignored_line(raw: str) -> bool:
    stripped = raw.strip()
    return not stripped or stripped.startswith("#")


def _next_content_index(lines: list[str], index: int) -> int:
    while index < len(lines) and _ignored_line(lines[index]):
        index += 1
    return index


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if value == "[]":
        return []
    if value == "{}":
        return {}
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _parse_block_scalar(lines: list[str], index: int, indent: int, style: str) -> tuple[str, int]:
    values: list[str] = []
    block_indent: int | None = None
    while index < len(lines):
        raw = lines[index]
        if not raw.strip():
            values.append("")
            index += 1
            continue
        current = _line_indent(raw)
        if current <= indent:
            break
        if block_indent is None:
            block_indent = current
        values.append(raw[min(block_indent, len(raw)):])
        index += 1
    if style == ">":
        folded: list[str] = []
        paragraph: list[str] = []
        for value in values:
            if value:
                paragraph.append(value.strip())
            else:
                if paragraph:
                    folded.append(" ".join(paragraph))
                    paragraph = []
                folded.append("")
        if paragraph:
            folded.append(" ".join(paragraph))
        return "\n".join(folded).rstrip("\n"), index
    return "\n".join(values).rstrip("\n"), index


def _parse_single_quoted(lines: list[str], index: int, indent: int, raw_value: str) -> tuple[str, int]:
    values = [raw_value[1:]]
    while values and not values[-1].endswith("'") and index < len(lines):
        raw = lines[index]
        if raw.strip() and _line_indent(raw) <= indent:
            break
        current = _line_indent(raw)
        values.append(raw[min(current, len(raw)):])
        index += 1
    text = "\n".join(values)
    if text.endswith("'"):
        text = text[:-1]
    return text.replace("''", "'"), index


def _parse_value(lines: list[str], index: int, indent: int, raw_value: str) -> tuple[Any, int]:
    value = raw_value.strip()
    if value in {"|", ">"}:
        return _parse_block_scalar(lines, index, indent, value)
    if value.startswith("'") and not (len(value) >= 2 and value.endswith("'")):
        return _parse_single_quoted(lines, index, indent, value)
    if value:
        return _parse_scalar(value), index
    child_index = _next_content_index(lines, index)
    if child_index < len(lines) and _line_indent(lines[child_index]) > indent:
        return _parse_block(lines, child_index, _line_indent(lines[child_index]))
    return None, index


def _parse_mapping(lines: list[str], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        index = _next_content_index(lines, index)
        if index >= len(lines):
            break
        raw = lines[index]
        current = _line_indent(raw)
        if current < indent:
            break
        if current > indent:
            break
        line = raw.strip()
        if line.startswith("- "):
            break
        if ":" not in line:
            raise ValueError(f"unsupported yaml line: {line}")
        key, raw_value = line.split(":", 1)
        value, index = _parse_value(lines, index + 1, current, raw_value)
        result[key.strip()] = value
    return result, index


def _parse_list_item(lines: list[str], index: int, indent: int, item: str) -> tuple[Any, int]:
    if not item:
        child_index = _next_content_index(lines, index)
        if child_index < len(lines) and _line_indent(lines[child_index]) > indent:
            return _parse_block(lines, child_index, _line_indent(lines[child_index]))
        return None, index
    if item in {"|", ">"}:
        return _parse_block_scalar(lines, index, indent, item)
    if item.startswith("|") and item[1:].strip() in {"-", "+"}:
        return _parse_block_scalar(lines, index, indent, "|")
    if item.startswith(">") and item[1:].strip() in {"-", "+"}:
        return _parse_block_scalar(lines, index, indent, ">")
    if ":" in item and not item.startswith(("'", '"')):
        key, raw_value = item.split(":", 1)
        value, index = _parse_value(lines, index, indent + 2, raw_value)
        result = {key.strip(): value}
        child_index = _next_content_index(lines, index)
        if child_index < len(lines) and _line_indent(lines[child_index]) > indent:
            child, index = _parse_block(lines, child_index, _line_indent(lines[child_index]))
            if isinstance(child, dict):
                result.update(child)
        return result, index
    return _parse_scalar(item), index


def _parse_list(lines: list[str], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        index = _next_content_index(lines, index)
        if index >= len(lines):
            break
        raw = lines[index]
        current = _line_indent(raw)
        if current != indent:
            break
        line = raw.strip()
        if not line.startswith("- "):
            break
        item, index = _parse_list_item(lines, index + 1, indent, line[2:].strip())
        result.append(item)
    return result, index


def _parse_block(lines: list[str], index: int, indent: int) -> tuple[Any, int]:
    index = _next_content_index(lines, index)
    if index >= len(lines):
        return {}, index
    if lines[index].strip().startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _read_simple_yaml(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    index = _next_content_index(lines, 0)
    if index >= len(lines):
        return {}
    parsed, index = _parse_block(lines, index, _line_indent(lines[index]))
    index = _next_content_index(lines, index)
    if index != len(lines):
        raise ValueError(f"unsupported yaml structure in {path}")
    if not isinstance(parsed, dict):
        raise ValueError(f"expected mapping in {path}")
    return parsed


def _quote_scalar(value: str) -> str:
    if value == "" or value != value.strip() or any(char in value for char in ":#[]{}\n"):
        return "'" + value.replace("'", "''") + "'"
    return value


def _dump_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return _quote_scalar(value)
    return _quote_scalar(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _dump_simple_yaml(value: Any, indent: int = 0) -> str:
    lines: list[str] = []
    prefix = " " * indent
    if isinstance(value, dict):
        for key, child in value.items():
            if child == []:
                lines.append(f"{prefix}{key}: []")
            elif child == {}:
                lines.append(f"{prefix}{key}: {{}}")
            elif isinstance(child, (dict, list)):
                lines.append(f"{prefix}{key}:")
                nested = _dump_simple_yaml(child, indent + 2)
                if nested:
                    lines.append(nested.rstrip("\n"))
            elif isinstance(child, str) and "\n" in child:
                lines.append(f"{prefix}{key}: |")
                lines.extend(f"{' ' * (indent + 2)}{line}" for line in child.splitlines())
            else:
                lines.append(f"{prefix}{key}: {_dump_scalar(child)}")
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, dict):
                if not child:
                    lines.append(f"{prefix}- {{}}")
                    continue
                items = list(child.items())
                first_key, first_value = items[0]
                if isinstance(first_value, (dict, list)):
                    lines.append(f"{prefix}- {first_key}:")
                    nested = _dump_simple_yaml(first_value, indent + 4)
                    if nested:
                        lines.append(nested.rstrip("\n"))
                elif isinstance(first_value, str) and "\n" in first_value:
                    lines.append(f"{prefix}- {first_key}: |")
                    lines.extend(f"{' ' * (indent + 4)}{line}" for line in first_value.splitlines())
                else:
                    lines.append(f"{prefix}- {first_key}: {_dump_scalar(first_value)}")
                rest = dict(items[1:])
                nested = _dump_simple_yaml(rest, indent + 2)
                if nested:
                    lines.append(nested.rstrip("\n"))
            elif isinstance(child, (dict, list)):
                lines.append(f"{prefix}-")
                nested = _dump_simple_yaml(child, indent + 2)
                if nested:
                    lines.append(nested.rstrip("\n"))
            elif isinstance(child, str) and "\n" in child:
                lines.append(f"{prefix}- |")
                lines.extend(f"{' ' * (indent + 2)}{line}" for line in child.splitlines())
            else:
                lines.append(f"{prefix}- {_dump_scalar(child)}")
    else:
        lines.append(prefix + _dump_scalar(value))
    return "\n".join(lines) + ("\n" if lines else "")
