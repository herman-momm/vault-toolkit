#!/usr/bin/env python3
"""Tests voor de verrijking van een boekpublicatie: omslag, tags, auteurs, kenmerken.

Waarom deze suite bestaat: tot 6 november 2026 konden `capacities-push.py` en
`wp-book-showcase.py` al een omslag, ISBN en uitgever aan, maar
`daily-book-fallback.py` gaf ze niet mee — en tags en Persoon-objecten bestonden
in het push-script helemaal niet. Het resultaat was een Boek-object met een lege
`coverImage`, lege `tags` en een onvolledige `auteur`, zonder dat ergens een
foutmelding verscheen. Precies het soort gat dat je alleen ziet als je het
eindresultaat naast de bedoeling legt, dus het hoort in een test.

Geen netwerk: de API-aanroepen worden vervangen door een opnemer die registreert
wat er zou zijn verstuurd.

Draai met: python3 tests/test_book_enrichment.py   (of tests/run-tests.sh)
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

HIER = os.path.dirname(os.path.abspath(__file__))
TOOLKIT = os.path.dirname(HIER)
SCRIPTS = os.path.join(TOOLKIT, "scripts")

fails = []


def check(naam, kreeg, wilde):
    if kreeg != wilde:
        fails.append(f"{naam}\n   kreeg: {kreeg!r}\n   wilde: {wilde!r}")
        print(f"FAIL {naam}")
    else:
        print(f"ok   {naam}")


def laad(bestand, naam):
    spec = importlib.util.spec_from_file_location(naam, os.path.join(SCRIPTS, bestand))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cap = laad("capacities-push.py", "cap")
runner = laad("daily-book-fallback.py", "runner")

# --- 1. auteursnamen splitsen ---------------------------------------------
print("--- auteursnamen ---")
check("twee auteurs met &", cap.split_authors("David Kertai & Jona Lendering"),
      ["David Kertai", "Jona Lendering"])
check("bijdragen-van telt als auteur",
      cap.split_authors("David Kertai (met bijdragen van Jona Lendering)"),
      ["David Kertai", "Jona Lendering"])
check("redactie-suffix verdwijnt",
      cap.split_authors("Luc Amkreutz en Sasja van der Vaart-Verschoof (red.)"),
      ["Luc Amkreutz", "Sasja van der Vaart-Verschoof"])
check("e.a. hoort niet bij de naam", cap.split_authors("Daan Juijn e.a."),
      ["Daan Juijn"])
check("et al. ook niet", cap.split_authors("A. Smith et al."), ["A. Smith"])
check("één auteur blijft één", cap.split_authors("Carlo Rovelli"), ["Carlo Rovelli"])
check("leeg veld geeft lege lijst", cap.split_authors(""), [])
check("dubbele naam telt één keer",
      cap.split_authors("Jona Lendering & Jona Lendering"), ["Jona Lendering"])

# --- 2. eigenschappen-payload ---------------------------------------------
print("\n--- payload voor PATCH /object/markdown ---")
verstuurd = []


def neppe_api(endpoint, data=None, method="GET", token=None):
    verstuurd.append((endpoint, method, data))
    return {"id": "nieuw-id"}


echte_api = cap.api_request
cap.api_request = neppe_api
try:
    verstuurd.clear()
    cap.update_book_properties("boek-1", ["fenicie", "alfabet"],
                               "https://example.com/omslag.png",
                               ["David Kertai", "Jona Lendering"], "tok")
    check("één PATCH verstuurd", len(verstuurd), 1)
    endpoint, methode, data = verstuurd[0]
    check("naar /object/markdown", endpoint, "/object/markdown")
    check("met PATCH", methode, "PATCH")
    md = data["markdown"]
    check("tags als kommalijst zonder haken", "tags: fenicie, alfabet" in md, True)
    check("omslag als kale URL",
          "coverImage: https://example.com/omslag.png" in md, True)
    check("auteurs als wikilinks",
          "auteur: [[David Kertai]], [[Jona Lendering]]" in md, True)

    # Een lokaal pad is geen URL: dan hoort coverImage weg te blijven.
    verstuurd.clear()
    cap.update_book_properties("boek-2", [], "/tmp/omslag.png", [], "tok")
    check("lokaal pad levert geen PATCH op", len(verstuurd), 0)

    # Niets te zetten -> geen aanroep.
    verstuurd.clear()
    check("niets te zetten geeft False",
          cap.update_book_properties("boek-3", [], "", [], "tok"), False)
    check("en verstuurt niets", len(verstuurd), 0)

    # --- 3. persoon opzoeken of aanmaken ----------------------------------
    print("\n--- Persoon-objecten ---")

    def api_met_treffer(endpoint, data=None, method="GET", token=None):
        verstuurd.append((endpoint, method, data))
        if endpoint == "/objects/search":
            return {"results": [{"id": "p1", "title": "Jona Lendering"}]}
        return {"id": "nieuw"}

    cap.api_request = api_met_treffer
    verstuurd.clear()
    check("bestaande persoon wordt gekoppeld",
          cap.find_or_create_person("Jona Lendering", "tok", "sp"), "Jona Lendering")
    check("en er wordt niets aangemaakt",
          [e for e, m, d in verstuurd if e == "/object"], [])

    verstuurd.clear()
    check("onbekende persoon wordt aangemaakt",
          cap.find_or_create_person("David Kertai", "tok", "sp"), "David Kertai")
    aangemaakt = [d for e, m, d in verstuurd if e == "/object"]
    check("precies één aanmaak-aanroep", len(aangemaakt), 1)
    check("met het Persoon-type",
          aangemaakt[0]["structureId"], cap.PERSON_STRUCTURE_ID)
    check("en de naam als titel",
          aangemaakt[0]["properties"]["title"]["title"]["value"], "David Kertai")
finally:
    cap.api_request = echte_api

# --- 4. tags en kenmerken uit de vault ------------------------------------
print("\n--- tags en boekkenmerken uit de vault ---")
vault = tempfile.mkdtemp()
try:
    os.makedirs(os.path.join(vault, "wiki", "sources"))
    os.makedirs(os.path.join(vault, "scripts"))
    with open(os.path.join(vault, "wiki", "sources", "boek.md"), "w",
              encoding="utf-8") as fh:
        fh.write("---\ntitle: \"Een boek\"\ntags: [boek, fenicie, alfabet]\n"
                 "published: \"2022\"\nisbn: \"9789000000000\"\n---\n\n# Een boek\n")
    with open(os.path.join(vault, "scripts", "book-metadata.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"ander.epub": {"isbn": "111", "publisher": "Uitgever A"}}, fh)

    check("tags uit de frontmatter, zonder 'boek'",
          runner.source_page_tags(vault, "sources/boek"), ["fenicie", "alfabet"])
    check("geen bronpagina geeft geen tags",
          runner.source_page_tags(vault, None), [])

    check("sidecar wint voor een bekend bestand",
          runner.book_metadata(vault, "ander.epub", None),
          {"isbn": "111", "publisher": "Uitgever A", "year": ""})
    check("frontmatter vult aan waar het sidecar zwijgt",
          runner.book_metadata(vault, "onbekend.pdf", "sources/boek"),
          {"isbn": "9789000000000", "publisher": "", "year": "2022"})
finally:
    shutil.rmtree(vault, ignore_errors=True)

# --- 5. omslag uit een PDF ------------------------------------------------
print("\n--- omslag uit een PDF ---")
tmp = tempfile.mkdtemp()
try:
    import subprocess
    try:
        import pymupdf
    except ImportError:
        pymupdf = None
    if pymupdf is None:
        print("ok   pymupdf ontbreekt — PDF-omslagtest overgeslagen")
    else:
        pdf = os.path.join(tmp, "testboek.pdf")
        doc = pymupdf.open()
        pagina = doc.new_page()
        pagina.insert_text((72, 144), "OMSLAG")
        doc.save(pdf)
        doc.close()
        uit = os.path.join(tmp, "omslag.png")
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "book-cover.py"), pdf, uit],
            capture_output=True, text=True)
        check("book-cover.py slaagt op een PDF", proc.returncode, 0)
        check("en schrijft een bestand", os.path.exists(uit), True)
        check("dat niet leeg is", os.path.getsize(uit) > 500, True)

    vreemd = os.path.join(tmp, "boek.txt")
    open(vreemd, "w").write("geen boek")
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "book-cover.py"), vreemd],
        capture_output=True, text=True)
    check("onbekende extensie wordt geweigerd", proc.returncode != 0, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# --- 6. het handoff-contract tussen deel A en deel B --------------------
# Deel A (dit script) schrijft de handoff, deel B (een promptrecept dat Claude
# uitvoert) leest hem. Die twee staan in verschillende bestanden en in
# verschillende talen, dus niets dwingt ze bij elkaar te blijven — behalve dit.
print("\n--- handoff-contract ---")
SLEUTELS = ["date", "epub", "slug", "title", "author", "year", "isbn",
            "publisher", "nonfictie", "medium", "coverpad", "exportmd",
            "exportpdf", "querypage", "sourcepage", "tags", "excerpt"]

tmp = tempfile.mkdtemp()
try:
    os.makedirs(os.path.join(tmp, "scripts"))
    velden = {k: f"waarde-{k}" for k in SLEUTELS}
    pad = runner.schrijf_handoff(tmp, velden)
    check("handoff heet .daily-handoff.json", os.path.basename(pad),
          ".daily-handoff.json")
    with open(pad, encoding="utf-8") as fh:
        terug = json.load(fh)
    check("handoff is geldige JSON met alle sleutels",
          sorted(terug.keys()), sorted(SLEUTELS))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# Het script moet ook echt kunnen stárten. Deze suite importeert losse functies
# en raakt `main()` nooit aan, waardoor een ontbrekende import er maandenlang in
# kan zitten — precies wat er gebeurde met `argparse`, dat pas opviel bij een
# echte aanroep in een vault.
import subprocess as _sub
_p = _sub.run([sys.executable, os.path.join(SCRIPTS, "daily-book-fallback.py"),
               "--help"], capture_output=True, text=True)
check("daily-book-fallback.py --help start zonder fout", _p.returncode, 0)
check("en noemt de noodknop", "--publiceer-zelf" in _p.stdout, True)

bron = open(os.path.join(SCRIPTS, "daily-book-fallback.py"),
            encoding="utf-8").read()
ontbreekt_in_a = [k for k in SLEUTELS if f'"{k}":' not in bron]
check("deel A schrijft elke sleutel uit het contract", ontbreekt_in_a, [])

WERKRUIMTE = os.path.dirname(TOOLKIT)
for vault in ("ClaudeVault", "HistVault", "GroeneVault"):
    p = os.path.join(WERKRUIMTE, vault, "scripts", "daily-book-prompt-B.md")
    if not os.path.exists(p):
        check(f"{vault} heeft een deel B-prompt", False, True)
        continue
    tekst = open(p, encoding="utf-8").read()
    # GroeneVault heeft een oudere B-prompt met een eigen veldenlijst; toets
    # daar alleen de velden die elke B nodig heeft om te kunnen publiceren.
    nodig = SLEUTELS if vault != "GroeneVault" else [
        "title", "author", "year", "isbn", "publisher", "coverpad", "exportmd"]
    mist = [k for k in nodig if k not in tekst]
    check(f"{vault}: deel B noemt elke sleutel die het nodig heeft", mist, [])
    check(f"{vault}: deel B popt de wachtrij", "book-queue.txt" in tekst, True)
    check(f"{vault}: deel B ruimt de handoff op",
          ".daily-handoff.json" in tekst, True)

print()
if fails:
    print(f"=== {len(fails)} TEST(S) GEFAALD ===")
    for f in fails:
        print(" - " + f)
    sys.exit(1)
print("=== alle verrijkingstests geslaagd ===")
