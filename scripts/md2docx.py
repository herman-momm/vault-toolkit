#!/usr/bin/env python3
"""
md2docx.py — bouwt een Word-document (.docx) in huisstijl uit een markdown-bestand uit exports/.

Gebruik:
    python3 scripts/md2docx.py exports/mijn-document.md [exports/ander.md ...]

Ondersteunt dezelfde markdown-subset als md2pdf.py: # t/m ### koppen, alinea's,
opsommingen (- en 1.), tabellen (GFM), blockquote-callouts, codeblokken,
horizontale lijnen, inline **vet**/*cursief*/`code`, [[wikilinks]] en inline
afbeeldingen (![alt](pad)). Huisstijl: accentkleur donkerblauw #1F3864.
"""

import os
import re
import sys

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ACCENT = RGBColor(0x1F, 0x38, 0x64)
ACCENT_LIGHT = "E8EDF5"
H3_COLOR = RGBColor(0x2F, 0x4F, 0x7F)
MUTED = RGBColor(0x5A, 0x5A, 0x5A)
RULE_HEX = "C7D0DE"
CODE_BG = "F2F3F5"

PAGE_W_CM, PAGE_H_CM = 21.0, 29.7  # A4
MARGIN_CM = 2.0
CONTENT_W_CM = PAGE_W_CM - 2 * MARGIN_CM


def shade_cell(cell, hex_color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def cell_border(cell, **kwargs):
    """kwargs: top/bottom/left/right -> dict(sz, color, val)."""
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge, spec in kwargs.items():
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), spec.get("val", "single"))
        el.set(qn("w:sz"), str(spec.get("sz", 4)))
        el.set(qn("w:color"), spec.get("color", "000000"))
        borders.append(el)
    tcPr.append(borders)


def add_page_number_field(paragraph):
    run = paragraph.add_run()
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    run._r.addnext(fld)
    t = OxmlElement("w:t")
    t.text = "1"
    run_el = OxmlElement("w:r")
    run_el.append(t)
    fld.append(run_el)


def set_bottom_border(paragraph, color=RULE_HEX, sz=12):
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(sz))
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), color)
    pBdr.append(bottom)
    pPr.append(pBdr)


REPLACEMENTS = {
    "⭐": "", "📘": "", "✅": "[v]", "✓": "v",
    "🔴": "[hoog]", "🟠": "[midden]", "🟢": "[laag]",
}


def clean(text: str) -> str:
    for src, dst in REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text


INLINE_TOKEN = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\[\[[^\]]+\]\]|\[[^\]]+\]\([^)]+\))")


def add_inline_runs(paragraph, text, base_size=9.5, color=None):
    """Parse **vet**, `code`, [[wikilink]], [tekst](link) en voeg runs toe."""
    text = clean(text)
    pos = 0
    for m in INLINE_TOKEN.finditer(text):
        if m.start() > pos:
            r = paragraph.add_run(text[pos:m.start()])
            r.font.size = Pt(base_size)
            if color:
                r.font.color.rgb = color
        token = m.group(0)
        if token.startswith("**"):
            r = paragraph.add_run(token[2:-2])
            r.bold = True
        elif token.startswith("`"):
            r = paragraph.add_run(token[1:-1])
            r.font.name = "Courier New"
            r.font.size = Pt(base_size - 0.5)
        elif token.startswith("[["):
            inner = token[2:-2]
            label = inner.split("|")[-1]
            r = paragraph.add_run(label)
        else:
            mm = re.match(r"\[([^\]]+)\]\(([^)]+)\)", token)
            r = paragraph.add_run(mm.group(1))
        r.font.size = Pt(base_size)
        if color:
            r.font.color.rgb = color
        pos = m.end()
    if pos < len(text):
        r = paragraph.add_run(text[pos:])
        r.font.size = Pt(base_size)
        if color:
            r.font.color.rgb = color


def add_image(doc, img_path, base_dir, max_h_cm=10.0):
    path = img_path if os.path.isabs(img_path) else os.path.join(base_dir, img_path)
    if not os.path.exists(path):
        sys.stderr.write(f"afbeelding niet gevonden: {path}\n")
        return
    from PIL import Image as PILImage
    with PILImage.open(path) as im:
        iw, ih = im.size
    ratio = iw / ih
    w_cm = CONTENT_W_CM
    h_cm = w_cm / ratio
    if h_cm > max_h_cm:
        h_cm = max_h_cm
        w_cm = h_cm * ratio
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(path, width=Cm(w_cm), height=Cm(h_cm))


def add_table(doc, rows):
    header, body = rows[0], rows[1:]
    ncols = len(header)
    table = doc.add_table(rows=1, cols=ncols)
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False

    weights = []
    for c in range(ncols):
        cells = [header[c]] + [r[c] for r in body if len(r) > c]
        longest = max((len(x) for x in cells), default=1)
        weights.append(max(6, min(longest, 60)))
    total = sum(weights)
    widths_cm = [CONTENT_W_CM * w / total for w in weights]

    hdr_cells = table.rows[0].cells
    for c, text in enumerate(header):
        hdr_cells[c].width = Cm(widths_cm[c])
        p = hdr_cells[c].paragraphs[0]
        add_inline_runs(p, text, base_size=9, color=ACCENT)
        for r in p.runs:
            r.bold = True
        shade_cell(hdr_cells[c], ACCENT_LIGHT)
        cell_border(hdr_cells[c], bottom={"sz": 8, "color": "1F3864"})

    for ri, row in enumerate(body):
        row = (row + [""] * ncols)[:ncols]
        cells = table.add_row().cells
        for c, text in enumerate(row):
            cells[c].width = Cm(widths_cm[c])
            p = cells[c].paragraphs[0]
            add_inline_runs(p, text, base_size=9)
            if ri % 2 == 1:
                shade_cell(cells[c], "FAFBFC")
            for edge in ("top", "bottom", "left", "right"):
                cell_border(cells[c], **{edge: {"sz": 3, "color": RULE_HEX}})
    return table


def add_callout(doc, lines):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    cell = table.rows[0].cells[0]
    cell.width = Cm(CONTENT_W_CM)
    shade_cell(cell, ACCENT_LIGHT)
    cell_border(cell, left={"sz": 18, "color": "1F3864"})
    cell.paragraphs[0].text = ""
    first = True
    for line in lines:
        if not line.strip():
            continue
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        add_inline_runs(p, line, base_size=9.5, color=RGBColor(0x22, 0x30, 0x4A))
    return table


def add_code(doc, block_lines):
    p = doc.add_paragraph()
    shading = OxmlElement("w:pPr")
    p._p.append(shading) if not p._p.find(qn("w:pPr")) else None
    for i, line in enumerate(block_lines):
        if i > 0:
            p.add_run().add_break()
        r = p.add_run(line if line else " ")
        r.font.name = "Courier New"
        r.font.size = Pt(8.5)
    pPr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), CODE_BG)
    pPr.append(shd)


def parse(doc, md: str, base_dir: str):
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        if not line.strip():
            i += 1
            continue

        # inline afbeelding
        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", line.strip())
        if m:
            add_image(doc, m.group(2), base_dir)
            i += 1
            continue

        # codeblok
        if line.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(clean(lines[i]))
                i += 1
            i += 1
            add_code(doc, block)
            continue

        # tabel
        if line.lstrip().startswith("|"):
            raw = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                raw.append(lines[i].strip())
                i += 1
            rows = []
            for r in raw:
                if re.fullmatch(r"\|[\s:\-\|]+\|", r):
                    continue
                cells = [c.strip() for c in r.strip("|").split("|")]
                rows.append(cells)
            if rows:
                add_table(doc, rows)
                doc.add_paragraph()
            continue

        # blockquote
        if line.startswith(">"):
            block = []
            while i < len(lines) and lines[i].startswith(">"):
                block.append(lines[i].lstrip(">").strip())
                i += 1
            add_callout(doc, block)
            doc.add_paragraph()
            continue

        # horizontale lijn
        if re.fullmatch(r"-{3,}|\*{3,}", line.strip()):
            p = doc.add_paragraph()
            set_bottom_border(p)
            i += 1
            continue

        # koppen
        hm = re.match(r"^(#{1,6})\s+(.*)$", line)
        if hm:
            level, text = len(hm.group(1)), hm.group(2)
            if level == 1:
                p = doc.add_paragraph()
                add_inline_runs(p, text, base_size=20, color=ACCENT)
                for r in p.runs:
                    r.bold = True
                rule = doc.add_paragraph()
                set_bottom_border(rule, color="1F3864", sz=18)
            elif level == 2:
                p = doc.add_paragraph()
                add_inline_runs(p, text, base_size=14, color=ACCENT)
                for r in p.runs:
                    r.bold = True
                p.paragraph_format.space_before = Pt(10)
            else:
                p = doc.add_paragraph()
                add_inline_runs(p, text, base_size=11.5, color=H3_COLOR)
                for r in p.runs:
                    r.bold = True
                p.paragraph_format.space_before = Pt(8)
            i += 1
            continue

        # opsommingen (-)
        if re.match(r"^\s*[-*]\s+", line):
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                txt = re.sub(r"^\s*[-*]\s+", "", lines[i].rstrip())
                p = doc.add_paragraph(style="List Bullet")
                add_inline_runs(p, txt, base_size=9.5)
                i += 1
            continue

        # opsommingen (1.)
        if re.match(r"^\s*\d+\.\s+", line):
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                txt = re.sub(r"^\s*\d+\.\s+", "", lines[i].rstrip())
                p = doc.add_paragraph(style="List Number")
                add_inline_runs(p, txt, base_size=9.5)
                i += 1
            continue

        # alinea (samenvoegen tot lege regel)
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|\s*[-*]\s|\s*\d+\.\s|\||>|```|-{3,}|!\[)", lines[i]):
            para.append(lines[i].rstrip())
            i += 1
        p = doc.add_paragraph()
        add_inline_runs(p, " ".join(para), base_size=9.5)
        p.paragraph_format.space_after = Pt(6)


def build(md_path: str) -> str:
    with open(md_path, encoding="utf-8") as f:
        md = f.read()

    docx_path = os.path.splitext(md_path)[0] + ".docx"
    doc = Document()

    section = doc.sections[0]
    section.page_width = Cm(PAGE_W_CM)
    section.page_height = Cm(PAGE_H_CM)
    section.left_margin = Cm(MARGIN_CM)
    section.right_margin = Cm(MARGIN_CM)
    section.top_margin = Cm(MARGIN_CM)
    section.bottom_margin = Cm(MARGIN_CM + 0.2)

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(9.5)

    footer_p = section.footer.paragraphs[0]
    footer_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer_p.text = ""
    run = footer_p.add_run()
    run.font.size = Pt(7.5)
    run.font.color.rgb = MUTED
    add_page_number_field(footer_p)

    parse(doc, md, base_dir=os.path.dirname(md_path) or ".")
    doc.save(docx_path)
    return docx_path


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    for path in args:
        out = build(path)
        print(f"gebouwd: {out}")
