#!/usr/bin/env python3
"""Lever een omslagafbeelding voor een boek in raw/, ongeacht het formaat.

Gebruik: python3 scripts/book-cover.py "raw/<boek>.epub|.pdf" [uitvoer.png]
Print het pad naar de afbeelding, of eindigt met een foutmelding op stderr.

Waarom dit bestaat: `epub-cover.py` haalt de cover uit de EPUB-container, maar
de helft van de boeken in de wachtrij zijn PDF's, en daar is geen cover-bestand
in te vinden — de omslag ís pagina 1. De dagelijkse boekenrun gaf daarom
helemaal geen omslag door, waardoor zowel het Capacities-Boek als de WordPress
Book Showcase zonder beeld bleef. Dit script maakt dat onderscheid één keer, op
één plek, zodat de aanroeper alleen nog een pad hoeft te krijgen.

Voor PDF's wordt pagina 1 gerenderd met pymupdf op 150 dpi. Dat is ruim genoeg
voor een omslagminiatuur en klein genoeg om snel te uploaden.
"""
import os
import re
import subprocess
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
DPI = int(os.environ.get("VAULT_COVER_DPI", "150"))


def vault_root():
    boven = os.path.dirname(HIER)
    for kand in (os.environ.get("VAULT_ROOT"), os.getcwd(), boven):
        if kand and os.path.isdir(os.path.join(kand, "raw")):
            return os.path.abspath(kand)
    return os.path.abspath(os.getcwd())


def slug_van(naam):
    basis = os.path.splitext(os.path.basename(naam))[0]
    return re.sub(r"[^A-Za-z0-9]+", "-", basis).strip("-").lower()[:44]


def uit_epub(pad, uitvoer):
    """Delegeer naar epub-cover.py; dat kent de containerstructuur al."""
    cmd = [sys.executable, os.path.join(HIER, "epub-cover.py"), pad]
    if uitvoer:
        cmd.append(uitvoer)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"epub-cover.py gaf geen omslag: {proc.stderr.strip() or proc.stdout.strip()}")
    gevonden = proc.stdout.strip().splitlines()
    if not gevonden:
        sys.exit("epub-cover.py gaf geen pad terug")
    return gevonden[-1].strip()


def uit_pdf(pad, uitvoer, vault):
    """Render pagina 1. De omslag van een boek-PDF is vrijwel altijd pagina 1."""
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # oudere installaties
        except ImportError:
            sys.exit("pymupdf niet beschikbaar — kan geen PDF-omslag renderen")

    doc = pymupdf.open(pad)
    if doc.page_count < 1:
        sys.exit("PDF heeft geen pagina's")
    pagina = doc.load_page(0)
    pix = pagina.get_pixmap(dpi=DPI)
    if not uitvoer:
        map_ = os.path.join(vault, "exports", "covers")
        os.makedirs(map_, exist_ok=True)
        uitvoer = os.path.join(map_, slug_van(pad) + ".png")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(uitvoer)) or ".", exist_ok=True)
    pix.save(uitvoer)
    doc.close()
    return uitvoer


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__.strip().splitlines()[2])
    pad = sys.argv[1]
    uitvoer = sys.argv[2] if len(sys.argv) >= 3 else ""
    vault = vault_root()

    if not os.path.isabs(pad) and not os.path.exists(pad):
        kand = os.path.join(vault, pad)
        if os.path.exists(kand):
            pad = kand
    if not os.path.exists(pad):
        sys.exit(f"bestand niet gevonden: {pad}")

    ext = os.path.splitext(pad)[1].lower()
    if ext == ".epub":
        print(uit_epub(pad, uitvoer))
    elif ext == ".pdf":
        print(uit_pdf(pad, uitvoer, vault))
    else:
        sys.exit(f"geen omslag te halen uit {ext or 'bestand zonder extensie'} "
                 "(alleen .epub en .pdf)")


if __name__ == "__main__":
    main()
