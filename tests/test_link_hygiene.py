#!/usr/bin/env python3
"""Tests voor de vault-hygiëne in daily-book-fallback.py.

Deze functies bestaan omdat een geautomatiseerde boekenrun op 5 november 2026
in HistVault 52 kapotte links en een duplicaat-bronpagina achterliet. Het model
schreef Obsidian-titellinks (`[[Doggerland]]`) in plaats van de vaultconventie
met pad (`[[entities/doggerland]]`), verzon "Zie ook"-pagina's die niet
bestonden, en linkte naar `[[exports/...]]` buiten de wiki.

De sanitizer vangt dat deterministisch op. Deze tests bewaken dat gedrag, zodat
een latere wijziging aan de runner het niet stil weer kapot maakt.

Draai met: python3 tests/test_link_hygiene.py   (of tests/run-tests.sh)
"""
import importlib.util
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
TOOLKIT = os.path.dirname(HIER)
RUNNER = os.path.join(TOOLKIT, "scripts", "daily-book-fallback.py")

sys.path.insert(0, HIER)
import fixture_vault  # noqa: E402

spec = importlib.util.spec_from_file_location("runner", RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

fails = []


def check(naam, kreeg, wilde):
    if kreeg != wilde:
        fails.append(f"{naam}\n   kreeg: {kreeg!r}\n   wilde: {wilde!r}")
        print(f"FAIL {naam}")
    else:
        print(f"ok   {naam}")


VAULT = fixture_vault.bouw()
try:
    pages = runner.wiki_pages(VAULT)
    verwacht = (fixture_vault.AANTAL_PAGINAS + len(fixture_vault.META) + 1)  # +1 template
    check("inventaris leest de hele wiki", len(pages), verwacht)

    # --- de sanitizer -------------------------------------------------------
    uit, ontlinkt = runner.sanitize_wikilinks("het land [[Doggerland]] lag droog", pages)
    check("titellink naar padvorm", uit, "het land [[entities/doggerland]] lag droog")
    check("  niets ontlinkt", ontlinkt, [])

    uit, _ = runner.sanitize_wikilinks("[[Luc Amkreutz]] en [[Krijn|de Zeeuwse vondst]]", pages)
    check("titellink met label", uit,
          "[[entities/luc-amkreutz]] en [[entities/krijn|de Zeeuwse vondst]]")

    uit, ontlinkt = runner.sanitize_wikilinks("- [[Mesolithische Wetlands en Subsistentie]]", pages)
    check("verzonnen pagina ontlinkt", uit, "- **Mesolithische Wetlands en Subsistentie**")
    check("  gerapporteerd als kandidaat", ontlinkt, ["Mesolithische Wetlands en Subsistentie"])

    uit, _ = runner.sanitize_wikilinks("[[Citizen Science|strandstruiners]]", pages)
    check("verzonnen pagina met label", uit, "**strandstruiners**")

    uit, ontlinkt = runner.sanitize_wikilinks("[[exports/doggerland-samenvatting|PDF-export]]", pages)
    check("exports-link ontlinkt", uit, "**PDF-export**")
    check("  gerapporteerd als kandidaat", ontlinkt, ["exports/doggerland-samenvatting"])

    uit, _ = runner.sanitize_wikilinks("[[https://example.org|bron]]", pages)
    check("URL-link ontlinkt", uit, "**bron**")

    uit, _ = runner.sanitize_wikilinks(
        r"| Schedelfragment [[Krijn\|Krijn]] (Zeeland) |", pages)
    check("escape in tabelcel behouden", uit,
          r"| Schedelfragment [[entities/krijn\|Krijn]] (Zeeland) |")

    uit, _ = runner.sanitize_wikilinks(
        "[[2026-11-05-doggerland-samenvatting|de analyse]]", pages)
    check("basename naar padvorm", uit,
          "[[queries/2026-11-05-doggerland-samenvatting|de analyse]]")

    uit, _ = runner.sanitize_wikilinks(
        "[[concepts/mesolithicum]] en [[entities/noordzee|de Noordzee]]", pages)
    check("al correcte padlink ongemoeid", uit,
          "[[concepts/mesolithicum]] en [[entities/noordzee|de Noordzee]]")

    uit, _ = runner.sanitize_wikilinks("[[raw/Doggerland.pdf]] en [[#Samenvatting]]", pages)
    check("raw/ en anker ongemoeid", uit, "[[raw/Doggerland.pdf]] en [[#Samenvatting]]")

    uit, _ = runner.sanitize_wikilinks("[[concepts/mesolithicum|concepts/mesolithicum]]", pages)
    check("overbodig label weg", uit, "[[concepts/mesolithicum]]")

    uit, _ = runner.sanitize_wikilinks("[[Doggerland]] en [[Doggerland|het gebied]]", pages)
    check("meerdere links in één regel", uit,
          "[[entities/doggerland]] en [[entities/doggerland|het gebied]]")

    # Titelmatch: de pagina heet krijn.md maar de frontmatter-title wijkt af.
    uit, _ = runner.sanitize_wikilinks("[[Krijn (Neanderthaler)]]", pages)
    check("frontmatter-title matcht ook", uit, "[[entities/krijn]]")

    # --- duplicaatwacht -----------------------------------------------------
    check("bestaande bronpagina gevonden",
          runner.existing_source_page(VAULT, fixture_vault.SOURCE_RAW),
          fixture_vault.SOURCE_PAGE)
    check("onbekend boek geeft None",
          runner.existing_source_page(VAULT, "Nooit-Bestaan-123.epub"), None)

    # --- frontmatter --------------------------------------------------------
    check("sources-telling gelezen", runner.frontmatter_sources(VAULT, "entities/krijn"), 3)
    check("ontbrekende pagina geeft 0", runner.frontmatter_sources(VAULT, "entities/niets"), 0)

    # --- index bijwerken ----------------------------------------------------
    qline = ("- [[queries/2026-11-06-test-samenvatting|Uitgebreide samenvatting: Testboek]]"
             " — testregel (bronnen: 0)")
    sline = "- [[sources/2026-11-06-test|Testboek (Auteur, 2026)]] — testregel (bronnen: 0)"
    toegevoegd = runner.update_index(VAULT, "2026-11-06",
                                     [("query", qline), ("source", sline)])
    check("beide regels geplaatst", sorted(toegevoegd), ["query", "source"])

    tekst = open(os.path.join(VAULT, "wiki", "index.md"), encoding="utf-8").read()
    check("queryregel aanwezig", qline in tekst, True)
    check("bronregel aanwezig", sline in tekst, True)
    check("subkop twee keer toegevoegd",
          tekst.count("**Automatisch toegevoegd (nog niet geordend)**"), 2)
    check("datum bijgewerkt", "*Laatste update: 2026-11-06*" in tekst, True)
    check("frontmatter bijgewerkt", "updated: 2026-11-06" in tekst.split("---")[1], True)
    check("paginatelling bijgewerkt",
          f"| Wiki-pagina's | {fixture_vault.AANTAL_PAGINAS} |" in tekst, True)
    check("entiteiten bijgewerkt",
          f"| Entiteiten | {fixture_vault.AANTAL_ENTITEITEN} |" in tekst, True)
    check("bronnen bijgewerkt",
          f"| Bronnen verwerkt | {fixture_vault.AANTAL_BRONNEN} |" in tekst, True)

    runner.update_index(VAULT, "2026-11-06", [("query", qline)])
    tekst2 = open(os.path.join(VAULT, "wiki", "index.md"), encoding="utf-8").read()
    check("idempotent: regel niet verdubbeld", tekst2.count(qline), 1)

    # ontbrekende index.md mag niet crashen
    os.remove(os.path.join(VAULT, "wiki", "index.md"))
    check("geen index.md: lege uitkomst, geen fout",
          runner.update_index(VAULT, "2026-11-06", [("query", qline)]), [])

    # --- uitvoerstappen: een mislukking moet geregistreerd worden -----------
    # De vijf publicatiestappen stonden op check=False, dus een gefaalde
    # publicatie liep stil mee in een run die "GESLAAGD" meldde. Dit toetst de
    # boekhouding die dat zichtbaar maakt.
    import stat as _stat
    import tempfile

    tmp = tempfile.mkdtemp()
    try:
        goed = os.path.join(tmp, "goed.sh")
        slecht = os.path.join(tmp, "slecht.sh")
        with open(goed, "w") as fh:
            fh.write("#!/bin/bash\nexit 0\n")
        with open(slecht, "w") as fh:
            fh.write("#!/bin/bash\nexit 7\n")
        for p in (goed, slecht):
            os.chmod(p, os.stat(p).st_mode | _stat.S_IEXEC)

        mislukt = []
        check("geslaagde stap geeft True",
              runner.voer_stap_uit("test-ok", goed, ["/bin/bash", goed], mislukt), True)
        check("geslaagde stap voegt niets toe", mislukt, [])

        check("falende stap geeft False",
              runner.voer_stap_uit("test-faal", slecht, ["/bin/bash", slecht], mislukt),
              False)
        check("falende stap wordt geregistreerd met exitcode", mislukt,
              ["test-faal — exitcode 7"])

        ontbreekt = os.path.join(tmp, "bestaat-niet.py")
        check("ontbrekend script geeft False",
              runner.voer_stap_uit("test-weg", ontbreekt,
                                   [sys.executable, ontbreekt], mislukt), False)
        check("ontbrekend script wordt geregistreerd i.p.v. stil overgeslagen",
              mislukt[-1], "test-weg — script niet gevonden")
        check("twee mislukkingen geteld", len(mislukt), 2)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
finally:
    fixture_vault.ruim_op(VAULT)

print()
if fails:
    print(f"=== {len(fails)} TEST(S) GEFAALD ===")
    for f in fails:
        print(" - " + f)
    sys.exit(1)
print("=== alle link-hygiënetests geslaagd ===")
