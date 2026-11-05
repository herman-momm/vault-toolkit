#!/usr/bin/env python3
"""Bouwt een synthetische vault in een tijdelijke map, voor de tests.

Waarom synthetisch en niet een echte vault: de eerste versie van deze tests
toetste tegen HistVault en controleerde letterlijk "608 pagina's" en "172
entiteiten". Die vault groeit elke dag, dus zulke tests gaan vals alarm geven
zodra iemand een bron verwerkt. Een fixture met een handvol bekende pagina's
blijft stabiel, en is bovendien leesbaar: je ziet in dit bestand precies welke
pagina's een test tot zijn beschikking heeft.

De inhoud is zo klein mogelijk gehouden, maar bevat wel alles waar de
hygiënefuncties op leunen: pagina's in alle vier de mappen, een frontmatter met
`title:` die van de bestandsnaam afwijkt, een `sources:`-telling, een
bronpagina die een raw-bestand noemt, meta-pagina's en een templates/-map (die
niet meegeteld horen te worden), en een index met de twee secties en de
statistiektabel die update_index bijwerkt.
"""
import os
import shutil
import tempfile

# Pagina's in de fixture: (relatief pad onder wiki/, frontmatter-title, sources-telling)
PAGES = [
    ("entities/doggerland", "Doggerland", 2),
    ("entities/luc-amkreutz", "Luc Amkreutz", 1),
    ("entities/krijn", "Krijn (Neanderthaler)", 3),
    ("entities/noordzee", "Noordzee", 1),
    ("concepts/mesolithicum", "Mesolithicum", 2),
    ("queries/2026-11-05-doggerland-samenvatting", "Uitgebreide samenvatting: Doggerland", 0),
]
SOURCE_PAGE = "sources/2021-doggerland-verdwenen-wereld"
SOURCE_RAW = "Doggerland.pdf"
META = ["index", "log", "overview", "status"]

# Verwachte tellingen — hier één keer vastgelegd, zodat een test ze niet
# zelf hoeft uit te rekenen en een wijziging in de fixture meteen opvalt.
AANTAL_BRONNEN = 1
AANTAL_ENTITEITEN = 4
AANTAL_CONCEPTEN = 1
AANTAL_QUERIES = 1
AANTAL_PAGINAS = AANTAL_BRONNEN + AANTAL_ENTITEITEN + AANTAL_CONCEPTEN + AANTAL_QUERIES

INDEX = """---
title: Wiki Index
type: overview
updated: 2026-01-01
---

# Index

---

## Bronnen (`wiki/sources/`)

- [[sources/2021-doggerland-verdwenen-wereld|Doggerland, verdwenen wereld]] — bestaande bron (bronnen: 1)

---

## Entiteiten (`wiki/entities/`)

- [[entities/doggerland|Doggerland]] — verdronken landschap (bronnen: 2)

---

## Concepten (`wiki/concepts/`)

- [[concepts/mesolithicum|Mesolithicum]] — middensteentijd (bronnen: 2)

---

## Vragen & Analyses (`wiki/queries/`)

- [[queries/2026-11-05-doggerland-samenvatting|Uitgebreide samenvatting: Doggerland]] — bestaande query (bronnen: 0)

---

## Statistieken

| | Aantal |
|---|---|
| Bronnen verwerkt | 0 |
| Wiki-pagina's | 0 |
| Entiteiten | 0 |
| Concepten | 0 |
| Opgeslagen queries | 0 |

*Laatste update: 2026-01-01*
"""


def _schrijf(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def bouw(root=None):
    """Zet de fixture neer en geef het vaultpad terug."""
    root = root or tempfile.mkdtemp(prefix="fixture-vault-")
    wiki = os.path.join(root, "wiki")

    for rel, title, sources in PAGES:
        soort = {"entities": "entity", "concepts": "concept",
                 "queries": "query"}[rel.split("/")[0]]
        _schrijf(os.path.join(wiki, rel + ".md"),
                 f"---\ntitle: \"{title}\"\ntype: {soort}\n"
                 f"created: 2026-01-01\nupdated: 2026-01-01\nsources: {sources}\n---\n\n"
                 f"# {title}\n\nFixture-inhoud.\n")

    _schrijf(os.path.join(wiki, SOURCE_PAGE + ".md"),
             "---\ntitle: \"Doggerland, verdwenen wereld\"\ntype: source\n"
             "created: 2026-01-01\nupdated: 2026-01-01\nsources: 1\n"
             f"raw: \"raw/{SOURCE_RAW}\"\n---\n\n# Doggerland, verdwenen wereld\n\n"
             "Fixture-bronpagina die het raw-bestand noemt.\n")

    for naam in META:
        _schrijf(os.path.join(wiki, naam + ".md"),
                 INDEX if naam == "index" else
                 f"---\ntitle: {naam}\ntype: overview\n---\n\n# {naam}\n")

    # templates/ hoort niet mee te tellen in de statistiek
    _schrijf(os.path.join(wiki, "templates", "source-template.md"),
             "---\ntitle: \"Brontitel\"\ntype: source\n---\n\n# Brontitel\n")

    # het raw-bestand waar de bronpagina naar verwijst
    _schrijf(os.path.join(root, "raw", SOURCE_RAW), "")

    return root


def ruim_op(root):
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    pad = bouw()
    print(pad)
