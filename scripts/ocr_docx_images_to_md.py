#!/usr/bin/env python3
"""Extract PNGs from .docx in document order and OCR to Markdown (chi_sim+eng)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
W_P = "{%s}p" % NS["w"]
W_T = "{%s}t" % NS["w"]
W_DRAWING = "{%s}drawing" % NS["w"]


def load_rels(z: zipfile.ZipFile) -> dict[str, str]:
    data = z.read("word/_rels/document.xml.rels")
    root = ET.fromstring(data)
    out: dict[str, str] = {}
    for rel in root:
        rid = rel.get("Id")
        target = rel.get("Target")
        if rid and target and "media/" in target:
            out[rid] = target.replace("\\", "/")
    return out


def paragraph_text(p: ET.Element) -> str:
    parts: list[str] = []
    for t in p.iter(W_T):
        if t.text:
            parts.append(t.text)
    return "".join(parts).strip()


def ordered_image_targets(z: zipfile.ZipFile, rels: dict[str, str]) -> list[str]:
    doc = z.read("word/document.xml")
    root = ET.fromstring(doc)
    body = root.find("w:body", NS)
    if body is None:
        return []
    order: list[str] = []
    seen: set[str] = set()
    for p in body.iter(W_P):
        text = paragraph_text(p)
        for blip in p.iter():
            if not blip.tag.endswith("}blip"):
                continue
            embed = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
            if not embed or embed not in rels:
                continue
            path = "word/" + rels[embed]
            if path in seen:
                continue
            seen.add(path)
            order.append(path)
    return order


def ocr_png(path: Path) -> str:
    r = subprocess.run(
        [
            "tesseract",
            str(path),
            "stdout",
            "-l",
            "chi_sim+eng",
            "--oem",
            "3",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        return f"[OCR failed: {r.stderr.strip() or r.returncode}]"
    return r.stdout.strip()


def first_line_as_heading(text: str) -> str | None:
    for line in text.splitlines():
        s = line.strip()
        if len(s) >= 4 and len(s) < 120:
            return s
    return None


def clean_block(text: str) -> str:
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: ocr_docx_images_to_md.py <file.docx> [out.md]", file=sys.stderr)
        return 2
    docx_path = Path(sys.argv[1]).resolve()
    out_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else docx_path.with_name(f"{docx_path.stem}_from_images.md")

    with zipfile.ZipFile(docx_path) as z, tempfile.TemporaryDirectory() as tmp:
        rels = load_rels(z)
        targets = ordered_image_targets(z, rels)
        if not targets:
            print("No embedded images found in document order.", file=sys.stderr)
            return 1

        doc_xml = z.read("word/document.xml")
        droot = ET.fromstring(doc_xml)
        dbody = droot.find("w:body", NS)
        title = "文档"
        if dbody is not None:
            for p in dbody.findall(W_P):
                if any(x.tag == W_DRAWING for x in p.iter()):
                    continue
                t = paragraph_text(p)
                if t:
                    title = t
                    break

        parts: list[str] = [
            f"# {title}",
            "",
            "_以下为从嵌入图片 OCR 提取的文字，按文档中图片顺序排列。部分字形可能因识别产生误差，请以原图为准。_",
            "",
        ]

        for i, tgt in enumerate(targets, start=1):
            data = z.read(tgt)
            img_path = Path(tmp) / f"slide_{i}.png"
            img_path.write_bytes(data)
            ocr_img = ocr_png(img_path)
            block = clean_block(ocr_img)
            sub = first_line_as_heading(block)
            parts.append(f"## 第 {i} 页")
            if sub:
                parts.append("")
                parts.append(f"**识别标题（首行）**: {sub}")
            parts.append("")
            parts.append("```")
            parts.append(block if block else "(无文字)")
            parts.append("```")
            parts.append("")

    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {out_path} ({len(targets)} images)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
