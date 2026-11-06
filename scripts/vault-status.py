#!/usr/bin/env python3
"""
vault-status.py — deterministische statusgenerator voor een LLM-wiki-vault.

Vault-agnostisch: dezelfde implementatie dient ClaudeVault, HistVault en
GroeneVault. Dit script GENEREERT niets vrijs, het SCANT de vault en vult een
vast sjabloon. Het signaleert alleen — beslissen (de "last mile") blijft
mensenwerk.

Gebruik:  python3 scripts/vault-status.py [--vault <pad>] [--sync-sources]
Effect:   (her)schrijft wiki/status.md met de actuele stand.

De vaultroot wordt bepaald via `--vault`, anders $VAULT_ROOT, anders de
huidige map, anders de map boven dit script — de eerste die een `wiki/`-map
bevat. Draaien vanuit de vaultroot blijft dus de normale werkwijze, maar een
verkeerde werkmap levert geen stille lege scan meer op.

Scant:
  - paginatellingen per type + drift t.o.v. de index-statistiektabel
  - ingest-achterstand: bestanden in raw/ zonder bronpagina in wiki/sources/
  - linkhygiëne: kapotte [[links]] en wees-pagina's
  - kennishiaten: de lijst uit wiki/overview.md
  - kandidaten/openstaande punten: uit de laatste logboekregels
"""
import re, glob, os, sys, datetime, unicodedata
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import vaultenv
except ImportError:          # losse kopie zonder vaultenv ernaast
    vaultenv = None


def nfc(s):
    """Normaliseer naar NFC.

    macOS bewaart bestandsnamen in NFD (bv. "Niño" = i + los accent), terwijl
    frontmatter en linktekst meestal NFC zijn. Zonder deze normalisatie
    matchen visueel identieke namen niet en meldt het dashboard een
    niet-bestaande ingest-achterstand of kapotte raw-link.
    """
    return unicodedata.normalize("NFC", s)


def vault_root(argv):
    """Bepaal de vaultroot: --vault, $VAULT_ROOT, cwd, of de map boven dit script."""
    explicit = None
    if "--vault" in argv:
        i = argv.index("--vault")
        if i + 1 < len(argv):
            explicit = argv[i + 1]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (explicit, os.environ.get("VAULT_ROOT"), os.getcwd(), here):
        if cand and os.path.isdir(os.path.join(cand, "wiki")):
            return os.path.abspath(cand)
    sys.stderr.write(
        "Geen vault gevonden (geen wiki/-map in --vault, $VAULT_ROOT, de huidige map "
        "of de map boven dit script). Draai het script vanuit de vaultroot.\n")
    sys.exit(2)


ROOT = vault_root(sys.argv)
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
                stem = nfc(t[4:])
                found = any(nfc(n).startswith(stem) for n in os.listdir(RAW))
                if not found and not log_only:
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
    covered = nfc("".join(open(f, encoding="utf-8").read()
                          for f in glob.glob(os.path.join(WIKI, "sources", "*.md"))))
    out = []
    for name in sorted(os.listdir(RAW)):
        if name.startswith(".") or name == "assets":
            continue
        if not os.path.isfile(os.path.join(RAW, name)):
            continue
        if nfc(name) not in covered:
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

# Geplande taken (macOS launchd). Elke run schrijft naar zijn logbestand,
# ongeacht de uitkomst, dus de mtime van dat log is het signaal "heeft gedraaid".
# Een gemiste of stilgevallen run was tot nu toe volledig onzichtbaar: launchd
# haalt een slag in als de Mac uit stond, maar een run die *faalt* meldt niets.
#
# (naam, geplande tijd, logbestand, max_leeftijd_uren of None voor alleen-info)
GEPLANDE_TAKEN = [
    ("Git-sync alle repo's", "03:00", "daily-github-sync.log", 36),
    ("Boek-orchestrator", "06:00", "daily-book-orchestrator.log", 36),
]
# Logs binnen de vault zelf, relatief aan de vaultroot.
VAULT_TAKEN = [
    ("Deze vault aan de beurt (boek)", "round-robin", "scripts/daily-book.log", None),
    ("Websitecontrole", "07:15", "scripts/website-check.log", 36),
]


def _log_mtime(path):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return None


# Markers die een falende run verraden. Bewust SCHREEUWEND waar de scripts zelf
# schreeuwen, zodat "0 inbox-sync(s) mislukt" in een geslaagde eindregel niet
# meetelt terwijl "!! inbox-sync MISLUKT" dat wel doet.
_FOUTMARKERS = ("!!", "GEWEIGERD", "FOUT", "MISLUKT", "FAIL", "Traceback",
                "fatal:")
# Start van een run, in de drie logformaten die hier langskomen.
_STARTMARKERS = ("START ", "start — ", "===== START")


def _laatste_run(pad, max_bytes=200_000):
    """Verdict over de LAATSTE run in een logbestand: ("goed"|"fout"|"onbekend", melding).

    Waarom dit nodig is: de mtime van het log zegt alleen dát er gedraaid is.
    Elke run schrijft naar zijn log, ook een run die faalt — dus een taak die
    elke nacht stukloopt bleef groen op dit dashboard. Op 6 nov 2026 weigerde de
    03:00-sync twee repo's en meldde het dashboard alles in orde.

    Daarom wordt het laatste runblok gelezen in plaats van alleen de tijdstempel.
    De drie logs hebben elk een eigen vorm, dus de aanpak is generiek: knip op de
    laatste startmarker, en vel dan een oordeel op (1) een `exit=<n>` die niet 0
    is, (2) een eindregel die een aantal mislukte onderdelen noemt, (3) een van
    de schreeuwmarkers. Geen van die drie gevonden en wél inhoud: goed.
    """
    try:
        grootte = os.path.getsize(pad)
        with open(pad, "rb") as fh:
            if grootte > max_bytes:
                fh.seek(grootte - max_bytes)
            tekst = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return "onbekend", "logbestand niet leesbaar"

    regels = [r for r in tekst.splitlines() if r.strip()]
    if not regels:
        return "onbekend", "logbestand is leeg"

    # Laatste runblok: vanaf de laatste startmarker, anders de staart.
    start = 0
    for i in range(len(regels) - 1, -1, -1):
        if any(m in regels[i] for m in _STARTMARKERS):
            start = i
            break
    else:
        start = max(0, len(regels) - 60)
    blok = regels[start:]

    # (1) Het SLOTWOORD van de run, van achteren naar voren gezocht: de laatste
    #     expliciete uitspraak over de uitkomst wint. Dit moet vóór de
    #     schreeuwmarkers komen, want een run mag onderweg best klagen zonder te
    #     mislukken: de boekentaak logt "!! CLI Subscription Limit bereikt" en
    #     draait dan verder op de Gemini-fallback, waarna hij met exit=0 slaagt.
    #     Op die regel alleen zou hij ten onrechte rood staan.
    for r in reversed(blok):
        m = list(re.finditer(r"exit=(\d+)", r))
        if m:
            codes = [x.group(1) for x in m]
            slecht = [c for c in codes if c != "0"]
            return ("fout", r.strip()[:160]) if slecht else ("goed", "")

        # Eindregel met een aantal mislukte onderdelen ("2 mislukt",
        # "1 inbox-sync(s) mislukt"). Het getal moet er direct vóór staan, met
        # ten hoogste één woord ertussen: een ruimere tussenruimte pakte in
        # "5 geslaagd, 0 mislukt" de 5 mee en maakte van elke geslaagde run een
        # mislukking.
        aantallen = [int(n) for n in
                     re.findall(r"(\d+)\s+(?:[A-Za-z()\-]+\s+)?mislukt", r)]
        if aantallen:
            return ("fout", r.strip()[:160]) if sum(aantallen) > 0 else ("goed", "")

        if "GESLAAGD" in r:
            return "goed", ""

    # (2) Geen slotwoord gevonden — dan zijn de schreeuwmarkers het enige
    #     signaal. Een run die halverwege is afgebroken valt hier.
    for r in blok:
        if any(m in r for m in _FOUTMARKERS):
            return "fout", r.strip()[:160]

    return "goed", ""


def automation_status():
    """Per geplande taak: wanneer draaide hij voor het laatst, en is dat te lang?

    Retourneert (rijen, aantal_problemen). Een rij is
    (naam, tijd, pad_of_None, laatste_datetime_of_None, leeftijd_uren_of_None,
     max_uren_of_None, staat, melding) met staat in
     {"ok", "oud", "ontbreekt", "info", "fout"}. "fout" betekent: hij heeft wél
    gedraaid, maar de laatste run is mislukt — dat was tot 6 nov 2026 onzichtbaar.
    """
    nu = datetime.datetime.now()
    rijen, problemen = [], 0

    taken = []
    for naam, tijd, bestand, maxuur in GEPLANDE_TAKEN:
        kandidaten = (vaultenv.config_candidates(bestand) if vaultenv
                      else [os.path.expanduser(f"~/.config/histvault/{bestand}")])
        pad = next((k for k in kandidaten if os.path.exists(k)), None)
        taken.append((naam, tijd, pad, maxuur))
    for naam, tijd, relpad, maxuur in VAULT_TAKEN:
        vol = os.path.join(ROOT, relpad)
        taken.append((naam, tijd, vol if os.path.exists(vol) else None, maxuur))

    for naam, tijd, pad, maxuur in taken:
        laatste = _log_mtime(pad) if pad else None
        if laatste is None:
            # Een ontbrekend log is alleen een probleem bij een taak die hier
            # hoort te draaien; de websitecontrole loopt niet in elke vault.
            if maxuur is not None and naam != "Websitecontrole":
                rijen.append((naam, tijd, pad, None, None, maxuur, "ontbreekt", ""))
                problemen += 1
            continue
        uren = (nu - laatste).total_seconds() / 3600
        uitkomst, melding = _laatste_run(pad)
        if uren > (maxuur or 10**9):
            # Te lang stil weegt zwaarder dan de uitkomst van die oude run.
            staat = "oud"
            problemen += 1
        elif uitkomst == "fout":
            staat = "fout"
            problemen += 1
        elif maxuur is None:
            staat = "info"
        else:
            staat = "ok"
        rijen.append((naam, tijd, pad, laatste, uren, maxuur, staat, melding))
    return rijen, problemen


def duur(uren):
    """Leeftijd als korte, leesbare tekst."""
    if uren < 1:
        return f"{int(uren * 60)} min"
    if uren < 48:
        return f"{uren:.0f} uur"
    return f"{uren / 24:.0f} dagen"


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
    automation, auto_problemen = automation_status()
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
    auto_tekst = ("alle geplande taken hebben recent gedraaid en zijn geslaagd"
                  if not auto_problemen
                  else f"{auto_problemen} geplande taak draaide te lang niet of is mislukt"
                  if auto_problemen == 1
                  else f"{auto_problemen} geplande taken draaiden te lang niet of zijn mislukt")
    L.append(f"- {health(not auto_problemen)} **Automatisering** — {auto_tekst}")
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

    L.append("## 6. Automatisering (geplande taken)")
    L.append("")
    L.append("Wanneer draaide elke launchd-taak voor het laatst, en is die run "
             "geslaagd? Elke run schrijft naar zijn logbestand, ook een run die "
             "faalt — de mtime zegt dus alleen *dát* hij gedraaid heeft. Daarom "
             "wordt ook het laatste runblok gelezen: een niet-nul `exit=`, een "
             "eindregel met mislukte onderdelen, of een `!!`/`FOUT`/`GEWEIGERD` "
             "maakt de regel rood. Staat de Mac op het geplande tijdstip uit, dan "
             "haalt launchd de slag in — de tijd hieronder is dus de echte starttijd.")
    L.append("")
    if automation:
        L.append("| Taak | Gepland | Laatste run | Leeftijd | Uitkomst | |")
        L.append("|---|---|---|---|---|---|")
        for naam, tijd, pad, laatste, uren, maxuur, staat, melding in automation:
            if staat == "ontbreekt":
                L.append(f"| {naam} | {tijd} | *geen logbestand gevonden* | — | — | 🔴 |")
                continue
            vlag = {"ok": "🟢", "oud": "🔴", "info": "ℹ️", "fout": "🔴"}[staat]
            uitkomst = {"ok": "geslaagd", "info": "geslaagd", "fout": "**mislukt**",
                        "oud": "te lang stil"}[staat]
            L.append(f"| {naam} | {tijd} | {laatste.strftime('%Y-%m-%d %H:%M')} | "
                     f"{duur(uren)} | {uitkomst} | {vlag} |")
        L.append("")
        for naam, tijd, pad, laatste, uren, maxuur, staat, melding in automation:
            if staat == "oud":
                L.append(f"- [ ] **{naam}** draaide {duur(uren)} niet (verwacht elke "
                         f"{maxuur} uur). Kijk in `{pad}`.")
            elif staat == "fout":
                L.append(f"- [ ] **{naam}** heeft {duur(uren)} geleden gedraaid, maar die "
                         f"run is mislukt. Uit het log: `{melding}` — zie `{pad}`.")
            elif staat == "ontbreekt":
                L.append(f"- [ ] **{naam}** heeft geen logbestand — heeft hij ooit gedraaid? "
                         f"Controleer met `launchctl list | grep dagzoom`.")
    else:
        L.append("*(Geen logbestanden van geplande taken gevonden.)*")
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
          f"{len(drift)} drift-punten, {auto_problemen} taken stil of mislukt, "
          f"{total_pages} pagina's")

if __name__ == "__main__":
    main()
