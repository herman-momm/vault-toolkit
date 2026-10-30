#!/usr/bin/env python3
"""Haal de front-cover uit een EPUB en schrijf die naar een bestand.

Gebruik: python3 scripts/epub-cover.py "raw/<boek>.epub" [uitvoer.jpg]
Zonder uitvoerpad schrijft het naar exports/covers/<slug>.<ext> en print dat pad.
"""
import zipfile, re, os, sys, json

# Dit script is vault-agnostic (vault-toolkit): draai het altijd vanuit de
# root van de vault die je verwerkt, nooit vanuit de toolkit-checkout zelf.
VAULT = os.getcwd()
BAD = ('bcover', 'back', 'map', 'kaart', 'katern', 'beeld', 'chapter', '_ch', '_map',
       'fig_', 'military', 'friedrich', 'hanged', 'mazzini', 'zeitung', 'illustr',
       'pg1', 'pg2', 'img0', 'portrait')


def score(name, isbn):
    b = os.path.basename(name).lower()
    if any(k in b for k in BAD):
        return -100
    s = 0
    if b in ('cover.jpg', 'cover.jpeg', 'cover.png', 'omslag.jpg', 'omslag.jpeg',
             'omslag.png', 'front.jpg', 'frontcover.jpg'):
        s += 100
    if 'cover' in b or 'omslag' in b or 'front' in b:
        s += 40
    digits = re.sub(r'\D', '', b)
    if isbn and isbn in digits:
        s += 60
    if len(digits) in (10, 13):
        s += 30
    return s


def find_cover(z, isbn=""):
    names = z.namelist()
    imgs = [n for n in names if n.lower().endswith(('.jpg', '.jpeg', '.png'))]
    if not imgs:
        return None
    opf = [n for n in names if n.lower().endswith('.opf')]
    if opf:
        o = z.read(opf[0]).decode('utf-8', 'ignore')
        base = os.path.dirname(opf[0])
        m = re.search(r'<item[^>]*properties="[^"]*cover-image[^"]*"[^>]*href="([^"]+)"', o)
        if m:
            p = os.path.normpath(os.path.join(base, m.group(1)))
            if p in imgs:
                return p
        mm = re.search(r'<meta[^>]*name="cover"[^>]*content="([^"]+)"', o)
        if mm:
            mi = re.search(r'<item[^>]*id="%s"[^>]*href="([^"]+)"' % re.escape(mm.group(1)), o)
            if mi:
                p = os.path.normpath(os.path.join(base, mi.group(1)))
                if p in imgs:
                    return p
    best = max(imgs, key=lambda n: score(n, isbn))
    if score(best, isbn) > 0:
        return best
    return max(imgs, key=lambda n: z.getinfo(n).file_size)


def main():
    if len(sys.argv) < 2:
        sys.exit("gebruik: epub-cover.py <epub> [uitvoer]")
    epub = sys.argv[1]
    if not os.path.isabs(epub):
        epub = os.path.join(VAULT, epub)
    fn = os.path.basename(epub)
    try:
        meta = json.load(open(os.path.join(VAULT, "scripts/book-metadata.json"), encoding="utf-8"))
        isbn = meta.get(fn, {}).get("isbn", "")
    except Exception:
        isbn = ""
    z = zipfile.ZipFile(epub)
    c = find_cover(z, isbn)
    if not c:
        sys.exit("geen cover gevonden in " + fn)
    ext = os.path.splitext(c)[1].lower().replace('.jpeg', '.jpg')
    if len(sys.argv) >= 3:
        out = sys.argv[2]
    else:
        slug = re.sub(r'[^A-Za-z0-9]+', '-', os.path.splitext(fn)[0]).strip('-').lower()[:44]
        os.makedirs(os.path.join(VAULT, "exports/covers"), exist_ok=True)
        out = os.path.join(VAULT, "exports/covers", slug + ext)
    open(out, 'wb').write(z.read(c))
    print(out)


if __name__ == "__main__":
    main()
