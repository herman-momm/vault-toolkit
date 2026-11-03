#!/usr/bin/env python3
"""
pdf-outline.py — Extraheer inhoudsopgave en structuur uit een PDF-bestand.

Bespaart 95%+ tokens bij boek-ingest: genereert een compacte Markdown-outline
met paginanummers en hoofdstuktitels zonder dat de LLM honderden PDF-pagina's
hoeft in te laden.

Gebruik:
  python3 pdf-outline.py boek.pdf              # Print structuur naar stdout
  python3 pdf-outline.py boek.pdf --save       # Slaat op als boek-outline.md
"""
import sys, os, argparse
import pypdf


def extract_outline(pdf_path: str):
    reader = pypdf.PdfReader(pdf_path)
    total_pages = len(reader.pages)
    title = os.path.basename(pdf_path).removesuffix(".pdf")
    
    # Probeer document info metadata
    meta = reader.metadata or {}
    doc_title = meta.title or title
    doc_author = meta.author or "Onbekend"

    lines = []
    lines.append(f"# Structuur: {doc_title}")
    lines.append(f"\n- **Bestand:** `{os.path.basename(pdf_path)}`")
    lines.append(f"- **Auteur:** {doc_author}")
    lines.append(f"- **Totale omvang:** {total_pages} pagina's\n")
    lines.append("## Inhoudsopgave (Outline)\n")

    def walk_outline(outline, level=0):
        found = False
        for item in outline:
            if isinstance(item, list):
                if walk_outline(item, level + 1):
                    found = True
            elif hasattr(item, "title"):
                found = True
                page_num = "?"
                try:
                    if hasattr(item, "page"):
                        page_num = reader.get_destination_page_number(item) + 1
                except Exception:
                    pass
                indent = "  " * level
                lines.append(f"{indent}- **{item.title}** (p. {page_num})")
        return found

    has_outline = False
    try:
        if reader.outline:
            has_outline = walk_outline(reader.outline)
    except Exception as e:
        lines.append(f"*(Fout bij lezen outline: {e})*")

    # Als er geen embedded outline is, scan de eerste 15 pagina's op inhoudsopgave
    if not has_outline:
        lines.append("*(Geen embedded bookmarks gevonden; tekstscan eerste 15 pagina's:)*\n")
        for i in range(min(15, total_pages)):
            txt = reader.pages[i].extract_text() or ""
            txt_lower = txt.lower()
            if any(k in txt_lower for k in ["inhoud", "contents", "table of contents", "hoofdstuk"]):
                lines.append(f"### Pagina {i + 1}\n```text")
                # Max 20 regels per inhoudspagina
                preview_lines = [l.strip() for l in txt.splitlines() if l.strip()][:25]
                lines.extend(preview_lines)
                lines.append("```\n")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Extraheer structuur en inhoudsopgave uit PDF")
    parser.add_argument("pdf", help="Pad naar PDF-bestand")
    parser.add_argument("--save", action="store_true", help="Sla outline op als <pdf>-outline.md")
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        sys.exit(f"Bestand niet gevonden: {args.pdf}")

    outline_text = extract_outline(args.pdf)

    if args.save:
        out_path = os.path.splitext(args.pdf)[0] + "-outline.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(outline_text + "\n")
        print(f"Outline opgeslagen: {out_path}")
    else:
        print(outline_text)


if __name__ == "__main__":
    main()
