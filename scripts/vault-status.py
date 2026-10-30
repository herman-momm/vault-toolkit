#!/usr/bin/env python3
"""
vault-status.py — deterministische statusgenerator voor de ClaudeVault.

Analoog aan het render-script van de "chief of staff"-masterclass: dit script
GENEREERT niets vrijs, het SCANT de vault en vult een vast sjabloon. Het
signaleert alleen — beslissen (de "last mile") blijft mensenwerk.

Gebruik:  python3 scripts/vault-status.py
Effect:   (her)schrijft wiki/status.md met de actuele stand.

Scant:
  - paginatellingen per type + drift t.o.v. de index-statistiektabel
  - ingest-achterstand: bestanden in raw/ zonder bronpagina in wiki/sources/
  - linkhygiëne: kapotte [[links]] en wees-pagina's
  - kennishiaten: de lijst uit wiki/overview.md
  - kandidaten/openstaande punten: uit de laatste logboekregels
"""
import re, glob, os, sys, datetime
from collections import defaultdict

# Dit script is vault-agnostic (vault-toolkit): draai het altijd vanuit de
# root van de vault die je verwerkt, nooit vanuit de toolkit-checkout zelf.
ROOT = os.getcwd()
WIKI = os.path.join(ROOT, "wiki")
RAW = os.path.join(ROOT, "raw")

LINK_RE = re.compile(r'\[\[([^\]|]+?)(?:\\?\|[^\]]*)?\]\]')
CODE_RE = re.compile(r'`[^`]*`')
# Meta-/generated pages: niet meegeteld als inhoud, niet als wees geflagd.
META = {"index", "log", "overview", "status"}

def rel(path):
    return os.path.relpath(path, WIKI).removesuffix(".md")

def load_pages():
    return {rel(f): f for f in glob.glob(os.path.join(WIKI, "**", "*.md"), recursive=True)}

def analyse_links(pages):
    incoming = defaultdict(set)
    broken = []
    for r, f in pages.items():
        if r.startswith("templates/"):
            continue
        # log.md is een historisch auditspoor: verwijzingen naar bewust ingetrokken
        # pagina's horen daar te blijven staan en gelden niet als kapotte link.
        log_only = r == "log"
        text = CODE_RE.sub("", open(f, encoding="utf-8").read())
        for m in LINK_RE.finditer(text):
            t = m.group(1).strip().rstrip("\\").removesuffix(".md")
            if t.startswith("#"):
                continue
            if t.startswith("raw/"):
                if not glob.glob(os.path.join(RAW, glob.escape(t[4:]) + "*")) and not log_only:
                    broken.append((r, t))
                continue
            if t in pages:
                incoming[t].add(r)
            else:
                hits = [p for p in pages if p.split("/")[-1] == t]
                if hits:
                    incoming[hits[0]].add(r)
                elif not log_only:
                    broken.append((r, t))
    orphans = [
        r for r in sorted(pages)
        if not r.startswith("templates/") and r not in META
        and not ({s for s in incoming.get(r, set()) if s not in META} - {r})
    ]
    return sorted(set(broken)), orphans

def ingest_backlog():
    covered = "".join(open(f, encoding="utf-8").read()
                      for f in glob.glob(os.path.join(WIKI, "sources", "*.md")))
    out = []
    for name in sorted(os.listdir(RAW)):
        if name.startswith(".") or name == "assets":
            continue
        if not os.path.isfile(os.path.join(RAW, name)):
            continue
        if name not in covered:
            out.append(name)
    return out

def section_lines(path, header):
    """Geef de bullet-regels onder een '## <header>'-kop tot de volgende '## '."""
    if not os.path.exists(path):
        return []
    lines = open(path, encoding="utf-8").read().splitlines()
    out, capture = [], False
    for ln in lines:
        if ln.startswith("## "):
            capture = header.lower() in ln.lower()
            continue
        if capture and ln.strip().startswith("- "):
            out.append(ln.strip()[2:].strip())
    return out

def index_stats():
    """Lees de statistiektabel uit de index."""
    path = os.path.join(WIKI, "index.md")
    stats = {}
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            m = re.match(r"\|\s*(Bronnen verwerkt|Wiki-pagina's|Entiteiten|Concepten|Opgeslagen queries)\s*\|\s*(\d+)\s*\|", ln)
            if m:
                stats[m.group(1)] = int(m.group(2))
    return stats

SRC_RE = re.compile(r"^sources:\s*(\d+)\s*$", re.M)

def count_supporting_sources(pages):
    """Bereken per concept-/entiteitspagina hoeveel BRONpagina's ernaar verwijzen.

    Definitie (zie CLAUDE.md): `sources: N` = het aantal pagina's in wiki/sources/
    dat naar deze pagina linkt. Dat is het enige controleerbare getal — alles wat
    handmatig wordt opgehoogd, loopt binnen enkele weken uit de pas.
    """
    actual = defaultdict(int)
    for r, f in pages.items():
        if not r.startswith("sources/"):
            continue
        text = CODE_RE.sub("", open(f, encoding="utf-8").read())
        targets = set()
        for m in LINK_RE.finditer(text):
            t = m.group(1).strip().rstrip("\\").removesuffix(".md")
            if t.startswith(("concepts/", "entities/")):
                targets.add(t)
        for t in targets:
            actual[t] += 1
    drift = []
    for r, f in sorted(pages.items()):
        if not r.startswith(("concepts/", "entities/")):
            continue
        m = SRC_RE.search(open(f, encoding="utf-8").read())
        if not m:
            continue
        declared, real = int(m.group(1)), actual.get(r, 0)
        if declared != real:
            drift.append((r, declared, real))
    return actual, drift

IDX_RE = re.compile(
    r"(\[\[((?:concepts|entities|sources|queries)/[^\]|#]+?)(?:\|[^\]]*)?\]\].*?\(bronnen:\s*)(\d+)(\))"
)

def index_source_drift(pages, fix=False):
    """Vergelijk de '(bronnen: N)'-annotaties in index.md met de frontmatter.

    De index houdt per regel een eigen telling bij; die mag niet los gaan lopen
    van de (berekende) frontmatter-waarde. Eén bron van waarheid.
    """
    path = os.path.join(WIKI, "index.md")
    if not os.path.exists(path):
        return []
    text = open(path, encoding="utf-8").read()
    declared = {}
    for r, f in pages.items():
        m = re.search(r"^sources:\s*(\d+)", open(f, encoding="utf-8").read(), re.M)
        if m:
            declared[r] = int(m.group(1))
    drift = []

    def sub(m):
        target = m.group(2).strip()
        real = declared.get(target)
        if real is None or real == int(m.group(3)):
            return m.group(0)
        drift.append((target, int(m.group(3)), real))
        return f"{m.group(1)}{real}{m.group(4)}" if fix else m.group(0)

    nieuw = IDX_RE.sub(sub, text)
    if fix and drift:
        open(path, "w", encoding="utf-8").write(nieuw)
    return drift

def sync_sources(pages, drift):
    """Schrijf de berekende telling weg in de frontmatter."""
    for r, declared, real in drift:
        f = pages[r]
        s = open(f, encoding="utf-8").read()
        s = SRC_RE.sub(f"sources: {real}", s, count=1)
        open(f, "w", encoding="utf-8").write(s)
    return len(drift)

def log_candidates():
    """Trek 'kandidaat'/'openstaand'/'nog niet'-flarden uit de laatste logregels."""
    path = os.path.join(WIKI, "log.md")
    if not os.path.exists(path):
        return []
    tail = open(path, encoding="utf-8").read().splitlines()[-80:]
    kw = re.compile(r"(kandidaat|openstaand\w*|suggestie\w*|nog (?:niet|te) verwerk\w*)", re.I)
    hits, seen = [], set()
    for ln in tail:
        m = kw.search(ln)
        if not m:
            continue
        # Neem de héle zin waarin het trefwoord staat, niet het staartje vanaf het
        # trefwoord — anders begint het fragment midden in een opsomming of link.
        plat = re.sub(r"\s+", " ", ln).strip()
        zinnen = re.split(r"(?<=[.;])\s+", plat)
        frag = next((z for z in zinnen if kw.search(z)), plat)
        # laat markdown-links en losse haakjes-restanten weg
        frag = re.sub(r"\[\[([^\]|]+\|)?([^\]]+)\]\]", r"\2", frag)
        frag = frag.lstrip("·-—) ").strip()
        if len(frag) < 25:
            continue
        frag = frag[:220].rstrip()
        key = frag.lower()[:60]
        if key not in seen:
            seen.add(key)
            hits.append(frag)
    return hits[-6:]

def main():
    pages = load_pages()
    counts = {
        "sources": len(glob.glob(os.path.join(WIKI, "sources", "*.md"))),
        "entities": len(glob.glob(os.path.join(WIKI, "entities", "*.md"))),
        "concepts": len(glob.glob(os.path.join(WIKI, "concepts", "*.md"))),
        "queries": len(glob.glob(os.path.join(WIKI, "queries", "*.md"))),
    }
    # inhoudspagina's = alles behalve templates en meta-/generated pages
    total_pages = len([p for p in pages
                       if not p.startswith("templates/") and p not in META])
    broken, orphans = analyse_links(pages)
    _, src_drift = count_supporting_sources(pages)
    if "--sync-sources" in sys.argv:
        sync_sources(pages, src_drift)
        _, src_drift = count_supporting_sources(pages)
        index_source_drift(pages, fix=True)
    idx_drift = index_source_drift(pages)
    backlog = ingest_backlog()
    gaps = section_lines(os.path.join(WIKI, "overview.md"), "Kennishiaten")
    candidates = log_candidates()
    stats = index_stats()
    today = datetime.date.today().isoformat()

    # drift tussen index-statistiek en werkelijkheid
    drift = []
    mapping = {
        "Wiki-pagina's": total_pages,
        "Entiteiten": counts["entities"],
        "Concepten": counts["concepts"],
        "Opgeslagen queries": counts["queries"],
    }
    for label, actual in mapping.items():
        claimed = stats.get(label)
        if claimed is not None and claimed != actual:
            drift.append(f"index zegt **{claimed}** {label}, geteld **{actual}**")

    def health(ok):
        return "🟢" if ok else "🔴"

    L = []
    L.append("---")
    L.append('title: "Vault Status"')
    L.append("type: overview")
    L.append(f"updated: {today}")
    L.append("dg-publish: false")
    L.append("---")
    L.append("")
    L.append("# Vault Status")
    L.append("")
    L.append("> [!note] Automatisch gegenereerd")
    L.append(f"> Deterministisch geschreven door `scripts/vault-status.py` op **{today}**. ")
    L.append("> Dit dashboard **signaleert**; het beslist niet. Draai het script opnieuw na elke ingest/lint. Bewerk niet met de hand.")
    L.append("")

    # --- Statusregel
    linkok = not broken and not orphans
    L.append("## Status in één oogopslag")
    L.append("")
    L.append(f"- {health(linkok)} **Linkhygiëne** — {len(broken)} kapotte links, {len(orphans)} wees-pagina's")
    L.append(f"- {health(not src_drift and not idx_drift)} **Bronentelling** — {len(src_drift)} pagina('s) met een afwijkende `sources:`-waarde; {len(idx_drift)} index-regel(s) met een afwijkende `(bronnen: N)`")
    L.append(f"- {health(not backlog)} **Ingest-achterstand** — {len(backlog)} raw-bestand(en) zonder bronpagina")
    L.append(f"- {health(not drift)} **Tellingen** — {'kloppen met de index' if not drift else 'wijken af van de index'}")
    L.append(f"- 📚 **Omvang** — {counts['sources']} bronnen · {counts['entities']} entiteiten · {counts['concepts']} concepten · {counts['queries']} queries · {total_pages} pagina's")
    L.append("")

    # --- Ingest-achterstand
    L.append("## 1. Ingest-achterstand (raw → sources)")
    L.append("")
    if backlog:
        L.append("Deze bestanden in `raw/` hebben nog geen bronpagina in `wiki/sources/`:")
        L.append("")
        for b in backlog:
            L.append(f"- [ ] `{b}`")
    else:
        L.append("Geen achterstand — elk bestand in `raw/` heeft een bronpagina. ✅")
    L.append("")

    # --- Linkhygiëne
    L.append("## 2. Linkhygiëne")
    L.append("")
    if broken:
        L.append("**Kapotte links** (bron → ontbrekend doel):")
        L.append("")
        for src, tgt in broken:
            L.append(f"- [ ] `{src}` → `[[{tgt}]]`")
        L.append("")
    if orphans:
        L.append("**Wees-pagina's** (geen inkomende links buiten index/log/overview):")
        L.append("")
        for o in orphans:
            L.append(f"- [ ] [[{o}]]")
        L.append("")
    if not broken and not orphans:
        L.append("Geen kapotte links, geen wezen. ✅")
        L.append("")

    # --- Drift
    L.append("## 3. Telling-drift")
    L.append("")
    if drift:
        L.append("De statistiektabel in `index.md` wijkt af van de werkelijke telling:")
        L.append("")
        for d in drift:
            L.append(f"- [ ] {d}")
    else:
        L.append("De index-statistiek klopt met de werkelijke telling. ✅")
    L.append("")

    # --- Kennishiaten
    L.append("## 3b. Bronentelling (`sources:`)")
    L.append("")
    if src_drift:
        L.append("Deze pagina's claimen een ander aantal bronnen dan het aantal bronpagina's dat naar ze verwijst.")
        L.append("Herstel met `python3 scripts/vault-status.py --sync-sources`.")
        L.append("")
        L.append("| Pagina | frontmatter | verwijzende bronnen |")
        L.append("|---|---|---|")
        for r, declared, real in src_drift[:25]:
            L.append(f"| `{r}` | {declared} | {real} |")
        if len(src_drift) > 25:
            L.append(f"| … | | *nog {len(src_drift) - 25} pagina's* |")
    elif idx_drift:
        L.append("De frontmatter klopt; alleen de index-annotaties lopen achter.")
    else:
        L.append("Elke `sources:`-waarde komt overeen met het aantal verwijzende bronpagina's. ✅")
    if idx_drift:
        L.append("")
        L.append(f"**Index-annotaties** — {len(idx_drift)} regel(s) met een `(bronnen: N)` die afwijkt van de pagina zelf:")
        L.append("")
        for target, claim, real in idx_drift[:10]:
            L.append(f"- `{target}` — index {claim}, pagina {real}")
        if len(idx_drift) > 10:
            L.append(f"- … nog {len(idx_drift) - 10} regel(s)")
    L.append("")

    L.append("## 4. Kennishiaten (uit overview.md)")
    L.append("")
    if gaps:
        for g in gaps:
            done = g.strip().startswith("~~") or g.lower().startswith("✓") or "gesloten" in g.lower()
            box = "x" if done else " "
            L.append(f"- [{box}] {g}")
    else:
        L.append("*(Geen `## Kennishiaten`-sectie gevonden in overview.md.)*")
    L.append("")

    # --- Kandidaten uit het log
    L.append("## 5. Openstaande kandidaten (uit recente logregels)")
    L.append("")
    if candidates:
        L.append("Genoteerd in `log.md`; beoordeel per stuk of het actie verdient:")
        L.append("")
        for c in candidates:
            L.append(f"- [ ] {c}")
    else:
        L.append("*(Geen recente kandidaat-notities in het log.)*")
    L.append("")

    L.append("---")
    L.append("")
    L.append("## Jouw oordeel")
    L.append("")
    L.append("De vakjes hierboven zijn *signalen*, geen opdrachten. Wat pak je vandaag op?")
    L.append("- **Ingest** ontbrekende bronnen, of parkeer ze bewust.")
    L.append("- **Repareer** kapotte links en ontwees pagina's (of draai `/lint`).")
    L.append("- **Sluit** een kennishiaat, of laat het staan met reden.")
    L.append("")
    L.append("## Zie ook")
    L.append("")
    L.append("- [[index|Index]] · [[overview|Overzicht]] · [[log|Logboek]]")
    L.append("")

    out_path = os.path.join(WIKI, "status.md")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print(f"status.md geschreven: {len(backlog)} ingest-achterstand, "
          f"{len(broken)} kapotte links, {len(orphans)} wezen, "
          f"{len(drift)} drift-punten, {total_pages} pagina's")

if __name__ == "__main__":
    main()
