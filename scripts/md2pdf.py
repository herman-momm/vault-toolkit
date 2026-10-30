#!/usr/bin/env python3
"""
md2pdf.py — bouwt een PDF in huisstijl uit een markdown-bestand uit exports/.

Gebruik:
    python3 scripts/md2pdf.py exports/mijn-document.md [exports/ander.md ...]

Ondersteunt: # t/m ### koppen, alinea's, opsommingen (- en 1.), tabellen (GFM),
blockquote-callouts, codeblokken, horizontale lijnen, en inline **vet**, *cursief*,
`code`. Emoji en pijlen worden omgezet naar tekst die de standaardfonts aankunnen.
"""

import os
import re
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    Image,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.utils import ImageReader

# --- huisstijl ---------------------------------------------------------------
ACCENT = colors.HexColor("#1F3864")      # donkerblauw — koppen
ACCENT_LIGHT = colors.HexColor("#E8EDF5")  # tabelkop / callout-achtergrond
RULE = colors.HexColor("#C7D0DE")
BODY = colors.HexColor("#1A1A1A")
MUTED = colors.HexColor("#5A5A5A")
CODE_BG = colors.HexColor("#F2F3F5")

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

# tekens die de standaard PDF-fonts niet kennen
REPLACEMENTS = {
    "→": "->", "⟶": "->", "←": "<-", "↔": "<->", "⇒": "=>",
    "≥": ">=", "≤": "<=", "≠": "!=", "≈": "~",
    "—": "-", "–": "-", "…": "...",
    "⭐": "", "📘": "", "⚠": "!", "✅": "[v]", "✓": "v",
    "🔴": "[hoog]", "🟠": "[midden]", "🟢": "[laag]",
    "“": '"', "”": '"', "‘": "'", "’": "'",
}


def clean(text: str) -> str:
    for src, dst in REPLACEMENTS.items():
        text = text.replace(src, dst)
    # overige niet-latin1 tekens (emoji e.d.) weglaten
    return "".join(ch for ch in text if ord(ch) < 0x2000 or ch in "·•€")


def inline(text: str) -> str:
    """Markdown-inline naar reportlab-markup."""
    text = clean(text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="8.5">\1</font>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\*\w])\*([^\*]+)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)   # links -> tekst
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)  # wikilink met alias
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    return text.strip()


# --- stijlen -----------------------------------------------------------------
_ss = getSampleStyleSheet()

S = {
    "title": ParagraphStyle("title", parent=_ss["Title"], fontName="Helvetica-Bold",
                            fontSize=19, leading=24, textColor=ACCENT,
                            alignment=TA_LEFT, spaceAfter=2),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13.5, leading=17,
                         textColor=ACCENT, spaceBefore=13, spaceAfter=5),
    "h3": ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=10.8, leading=14,
                         textColor=colors.HexColor("#2F4F7F"), spaceBefore=9, spaceAfter=3),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.4, leading=13.8,
                           textColor=BODY, spaceAfter=6),
    "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=9.4, leading=13.4,
                             textColor=BODY, leftIndent=11, bulletIndent=2, spaceAfter=2.5),
    "quote": ParagraphStyle("quote", fontName="Helvetica", fontSize=9.4, leading=13.6,
                            textColor=colors.HexColor("#22304A"), leftIndent=7, rightIndent=5,
                            spaceBefore=2, spaceAfter=2),
    "th": ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8.4, leading=11.2,
                         textColor=ACCENT),
    "td": ParagraphStyle("td", fontName="Helvetica", fontSize=8.4, leading=11.2,
                         textColor=BODY),
}


def table_flowable(rows):
    """GFM-tabel -> reportlab Table met kolombreedtes naar rato van inhoud."""
    header, body = rows[0], rows[1:]
    ncols = len(header)

    weights = []
    for c in range(ncols):
        cells = [header[c]] + [r[c] for r in body if len(r) > c]
        longest = max((len(x) for x in cells), default=1)
        weights.append(max(6, min(longest, 60)))
    total = sum(weights)
    widths = [CONTENT_W * w / total for w in weights]

    data = [[Paragraph(inline(c), S["th"]) for c in header]]
    for r in body:
        r = (r + [""] * ncols)[:ncols]
        data.append([Paragraph(inline(c), S["td"]) for c in r])

    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT_LIGHT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, ACCENT),
        ("GRID", (0, 0), (-1, -1), 0.35, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFBFC")]),
    ]))
    return t


def callout(lines):
    """Blockquote -> lichtblauw kader met accentbalk links."""
    inner = [Paragraph(inline(l), S["quote"]) for l in lines if l.strip()]
    t = Table([[inner]], colWidths=[CONTENT_W], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT_LIGHT),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def image_flowable(img_path, max_h=110 * mm):
    """Schaal een inline afbeelding proportioneel; centreer in de body."""
    iw, ih = ImageReader(img_path).getSize()
    scale = min(CONTENT_W / iw, max_h / ih)
    img = Image(img_path, width=iw * scale, height=ih * scale)
    img.hAlign = "CENTER"
    return img


def parse(md: str, base_dir: str = "."):
    """Markdown -> lijst van flowables."""
    flow = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        if not line.strip():
            i += 1
            continue

        # inline afbeelding: ![alt](pad)
        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", line.strip())
        if m:
            src = m.group(2)
            img_path = src if os.path.isabs(src) else os.path.join(base_dir, src)
            if os.path.exists(img_path):
                try:
                    flow.append(Spacer(1, 4))
                    flow.append(image_flowable(img_path))
                    flow.append(Spacer(1, 8))
                except Exception as e:
                    sys.stderr.write(f"afbeelding overgeslagen ({src}): {e}\n")
            else:
                sys.stderr.write(f"afbeelding niet gevonden: {img_path}\n")
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
            pre = Preformatted("\n".join(block), ParagraphStyle(
                "code", fontName="Courier", fontSize=8.2, leading=10.8,
                textColor=BODY, backColor=CODE_BG, borderPadding=6,
                leftIndent=2, spaceBefore=3, spaceAfter=7))
            flow.append(pre)
            continue

        # tabel
        if line.lstrip().startswith("|"):
            raw = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                raw.append(lines[i].strip())
                i += 1
            rows = []
            for r in raw:
                if re.fullmatch(r"\|[\s:\-\|]+\|", r):   # scheidingsrij
                    continue
                cells = [c.strip() for c in r.strip("|").split("|")]
                rows.append(cells)
            if rows:
                flow.append(Spacer(1, 3))
                flow.append(table_flowable(rows))
                flow.append(Spacer(1, 8))
            continue

        # blockquote
        if line.startswith(">"):
            block = []
            while i < len(lines) and lines[i].startswith(">"):
                block.append(lines[i].lstrip(">").strip())
                i += 1
            flow.append(Spacer(1, 3))
            flow.append(callout(block))
            flow.append(Spacer(1, 8))
            continue

        # horizontale lijn
        if re.fullmatch(r"-{3,}|\*{3,}", line.strip()):
            flow.append(Spacer(1, 4))
            flow.append(HRFlowable(width="100%", thickness=0.6, color=RULE,
                                   spaceBefore=2, spaceAfter=8))
            i += 1
            continue

        # koppen
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level, text = len(m.group(1)), m.group(2)
            if level == 1:
                flow.append(Paragraph(inline(text), S["title"]))
                flow.append(HRFlowable(width="100%", thickness=1.4, color=ACCENT,
                                       spaceBefore=4, spaceAfter=10))
            elif level == 2:
                flow.append(KeepTogether([Paragraph(inline(text), S["h2"])]))
            else:
                flow.append(KeepTogether([Paragraph(inline(text), S["h3"])]))
            i += 1
            continue

        # opsommingen
        if re.match(r"^\s*[-*]\s+", line):
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                txt = re.sub(r"^\s*[-*]\s+", "", lines[i].rstrip())
                flow.append(Paragraph(inline(txt), S["bullet"], bulletText="•"))
                i += 1
            flow.append(Spacer(1, 5))
            continue

        if re.match(r"^\s*\d+\.\s+", line):
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                num = re.match(r"^\s*(\d+)\.", lines[i]).group(1)
                txt = re.sub(r"^\s*\d+\.\s+", "", lines[i].rstrip())
                flow.append(Paragraph(inline(txt), S["bullet"], bulletText=f"{num}."))
                i += 1
            flow.append(Spacer(1, 5))
            continue

        # alinea (samenvoegen tot lege regel)
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|\s*[-*]\s|\s*\d+\.\s|\||>|```|-{3,})", lines[i]):
            para.append(lines[i].rstrip())
            i += 1
        flow.append(Paragraph(inline(" ".join(para)), S["body"]))

    return flow


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(PAGE_W - MARGIN, 12 * mm, f"{doc.page}")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, 16 * mm, PAGE_W - MARGIN, 16 * mm)
    canvas.restoreState()


def cover_flowable(cover_path, max_h=95 * mm):
    """Schaal de cover proportioneel; centreer bovenaan."""
    iw, ih = ImageReader(cover_path).getSize()
    scale = min(CONTENT_W / iw, max_h / ih)
    img = Image(cover_path, width=iw * scale, height=ih * scale)
    img.hAlign = "CENTER"
    return img


def build(md_path: str, cover: str = None) -> str:
    with open(md_path, encoding="utf-8") as f:
        md = f.read()

    pdf_path = os.path.splitext(md_path)[0] + ".pdf"
    doc = BaseDocTemplate(pdf_path, pagesize=A4,
                          leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN, bottomMargin=22 * mm,
                          title=os.path.basename(pdf_path))
    frame = Frame(MARGIN, 22 * mm, CONTENT_W, PAGE_H - MARGIN - 22 * mm, id="main")
    doc.addPageTemplates([PageTemplate(id="std", frames=[frame], onPage=footer)])
    story = parse(md, base_dir=os.path.dirname(md_path) or ".")
    if cover and os.path.exists(cover):
        try:
            story = [cover_flowable(cover), Spacer(1, 10)] + story
        except Exception as e:
            sys.stderr.write(f"cover overgeslagen: {e}\n")
    doc.build(story)
    return pdf_path


if __name__ == "__main__":
    args = sys.argv[1:]
    cover = None
    if "--cover" in args:
        i = args.index("--cover")
        cover = args[i + 1]
        del args[i:i + 2]
    if not args:
        print(__doc__)
        sys.exit(1)
    for path in args:
        out = build(path, cover=cover)
        print(f"gebouwd: {out}")
