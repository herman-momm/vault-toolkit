#!/usr/bin/env python3
"""Controleert de Capacities-credentials: werkt de sleutel, en lekt hij nergens?

Bedoeld om vóór én na een rotatie te draaien. Drie vragen:

  1. WAAR komt de sleutel vandaan? vaultenv kent meerdere bronnen en een env-var
     gaat altijd vóór het bestand — een vergeten `export CAPACITIES_TOKEN` zou
     een verse sleutel in het bestand stil overrulen.
  2. WERKT hij? Een read-only `GET /spaces` tegen api.capacities.io, en de vraag
     of de geconfigureerde space daarin voorkomt.
  3. LEKT hij? Een scan van de vijf repo's — werkmap én volledige git-historie
     over alle refs — op iets in de vorm van een Capacities-sleutel, buiten het
     credentialbestand zelf.

De sleutelwaarde wordt nooit geprint; alleen een sha256-vingerafdruk van acht
tekens, genoeg om twee sleutels te onderscheiden zonder er één te tonen.

Gebruik:
  python3 scripts/capacities-check.py            # alles
  python3 scripts/capacities-check.py --no-api   # sla de netwerkcheck over

Exitcode 0 als alles klopt, 1 bij een probleem.
"""
import argparse
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultenv

BASE_URL = "https://api.capacities.io"
KEYS = ("CAPACITIES_TOKEN", "CAPACITIES_SPACE_ID")
# Dezelfde vorm die de secret-rem van daily-github-sync.sh gebruikt.
SLEUTELVORM = re.compile(r"cap-api-[A-Za-z0-9_-]{16,}")
REPOS = ("ClaudeVault", "HistVault", "GroeneVault", "vault-toolkit",
         "dagzoom-vaults-hub")
WERKRUIMTE = os.path.join(
    os.path.expanduser("~"), "Library", "Mobile Documents",
    "com~apple~CloudDocs", "LLM-Workspace")

fouten = 0


def ok(tekst):
    print(f"ok   {tekst}")


def faal(tekst):
    global fouten
    print(f"FAAL {tekst}")
    fouten += 1


def vingerafdruk(waarde):
    return hashlib.sha256(waarde.encode("utf-8")).hexdigest()[:8]


def kop(tekst):
    print(f"\n--- {tekst} ---")


def tls_context():
    """Een SSL-context MET certificaatcontrole, plus waar de bundel vandaan komt.

    De python.org-build op deze Mac wijst `openssl_cafile` naar een bestand dat
    niet bestaat ("Install Certificates.command" is nooit gedraaid), waardoor
    elke https-aanroep faalt met CERTIFICATE_VERIFY_FAILED. Dát is de reden dat
    de andere scripts hier `ssl._create_unverified_context()` gebruiken. Een
    bundel is er wél — via certifi of de systeembundel van macOS — dus die
    zoeken we op in plaats van de controle uit te zetten.
    """
    try:
        import certifi
        pad = certifi.where()
        if os.path.isfile(pad):
            return ssl.create_default_context(cafile=pad), f"certifi ({pad})"
    except ImportError:
        pass

    for pad in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"):
        if os.path.isfile(pad):
            return ssl.create_default_context(cafile=pad), f"systeembundel ({pad})"

    ctx = ssl.create_default_context()
    standaard = ssl.get_default_verify_paths().cafile
    return ctx, f"standaard ({standaard or 'geen bundel gevonden'})"


def controleer_herkomst():
    kop("1. herkomst van de sleutel")

    # Een env-var gaat vóór het bestand; dat is de stille overrule.
    uit_omgeving = os.environ.get("CAPACITIES_TOKEN")
    if uit_omgeving:
        faal("CAPACITIES_TOKEN staat in de omgeving en overrult het bestand "
             f"(vingerafdruk {vingerafdruk(uit_omgeving)}). Unset hem, of werk "
             "hem net zo goed bij als het bestand.")
    else:
        ok("geen CAPACITIES_TOKEN in de omgeving — het bestand is leidend")

    # Welke kandidaatpaden bestaan? Meer dan één is verwarrend, geen is fataal.
    kandidaten = [
        os.path.join(os.path.expanduser("~"), ".config", naam, "capacities.env")
        for naam in ("claudevault", "histvault", "groenevault", "vaults")
    ]
    bestaand = [p for p in kandidaten if os.path.isfile(p)]
    if len(bestaand) == 1:
        ok(f"één credentialbestand: {bestaand[0]}")
    elif not bestaand:
        faal("geen enkel capacities.env gevonden op de bekende paden")
    else:
        faal("meerdere capacities.env-bestanden — vaultenv kiest er één en de "
             "andere raken ongemerkt achter:")
        for p in bestaand:
            print(f"       {p}")

    cfg = vaultenv.load_config("capacities.env", KEYS)
    token = cfg.get("CAPACITIES_TOKEN")
    space = cfg.get("CAPACITIES_SPACE_ID")

    if token:
        ok(f"CAPACITIES_TOKEN gevonden (vingerafdruk {vingerafdruk(token)})")
        if not SLEUTELVORM.fullmatch(token.strip()):
            faal("de waarde heeft niet de vorm cap-api-<minstens 16 tekens> — "
                 "staat er misschien een afgekapte of ingepakte waarde?")
    else:
        faal("geen CAPACITIES_TOKEN in het credentialbestand")
    if space:
        ok(f"CAPACITIES_SPACE_ID gevonden ({space})")
    else:
        faal("geen CAPACITIES_SPACE_ID in het credentialbestand")

    return token, space


def controleer_api(token, space):
    kop("2. werkt de sleutel?")
    if not token:
        faal("geen sleutel om te testen")
        return

    req = urllib.request.Request(
        f"{BASE_URL}/spaces",
        headers={"Authorization": f"Bearer {token}",
                 "User-Agent": "vault-toolkit/1.0"},
        method="GET",
    )
    # Bewust mét certificaatcontrole: capacities-push.py gebruikt hier
    # ssl._create_unverified_context(), en dat hoort niet bij een aanroep
    # waarin een bearer-token meegaat.
    ctx, bundelbron = tls_context()
    print(f"     CA-bundel: {bundelbron}")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            faal(f"de sleutel wordt geweigerd (HTTP {e.code}) — is hij al "
                 "geroteerd en nog niet bijgewerkt in het bestand?")
        else:
            faal(f"API-fout HTTP {e.code}: {e.reason}")
        return
    except Exception as e:  # netwerk, DNS, TLS
        faal(f"kon api.capacities.io niet bereiken: {e}")
        return

    spaces = data.get("spaces", data if isinstance(data, list) else [])
    ids = [s.get("id") for s in spaces if isinstance(s, dict)]
    ok(f"sleutel geaccepteerd — {len(ids)} space(s) zichtbaar")
    if space and ids and space not in ids:
        faal(f"de geconfigureerde space {space} zit niet in de lijst — "
             "hoort deze sleutel bij een ander account?")
    elif space and space in ids:
        ok(f"de geconfigureerde space {space} is bereikbaar met deze sleutel")


def scan_repos(env_paden):
    kop("3. lekt de sleutel ergens?")
    if not os.path.isdir(WERKRUIMTE):
        faal(f"werkruimte niet gevonden: {WERKRUIMTE}")
        return

    for repo in REPOS:
        pad = os.path.join(WERKRUIMTE, repo)
        if not os.path.isdir(os.path.join(pad, ".git")):
            print(f"     {repo}: geen git-repo, overgeslagen")
            continue

        # Werkmap: alles behalve .git en het credentialbestand zelf.
        treffers = []
        for root, dirs, files in os.walk(pad):
            dirs[:] = [d for d in dirs if d != ".git"]
            for naam in files:
                f = os.path.join(root, naam)
                if f in env_paden:
                    continue
                try:
                    if os.path.getsize(f) > 2_000_000:
                        continue
                    with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                        if SLEUTELVORM.search(fh.read()):
                            treffers.append(os.path.relpath(f, pad))
                except OSError:
                    continue

        # Historie over ALLE refs, niet alleen main: de sleutel zat eerder in
        # checkpoint-refs van een editor-extensie, buiten elke branch.
        try:
            uit = subprocess.run(
                ["git", "-C", pad, "log", "--all", "--oneline",
                 "-G", r"cap-api-[A-Za-z0-9_-]{16,}"],
                capture_output=True, text=True, timeout=180)
            historie = [r for r in uit.stdout.splitlines() if r.strip()]
        except Exception as e:
            historie = []
            faal(f"{repo}: kon de historie niet doorzoeken ({e})")

        if treffers or historie:
            faal(f"{repo}: {len(treffers)} bestand(en) in de werkmap, "
                 f"{len(historie)} commit(s) in de historie")
            for t in treffers[:5]:
                print(f"       werkmap:  {t}")
            for h in historie[:5]:
                print(f"       historie: {h}")
        else:
            ok(f"{repo}: schoon (werkmap en alle refs)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-api", action="store_true",
                   help="sla de netwerkcheck tegen api.capacities.io over")
    args = p.parse_args()

    print("Capacities-credentialcontrole")
    token, space = controleer_herkomst()
    if args.no_api:
        kop("2. werkt de sleutel?")
        print("     overgeslagen (--no-api)")
    else:
        controleer_api(token, space)

    env_paden = {
        os.path.join(os.path.expanduser("~"), ".config", naam, "capacities.env")
        for naam in ("claudevault", "histvault", "groenevault", "vaults")
    }
    scan_repos(env_paden)

    print()
    if fouten:
        print(f"=== {fouten} probleem(en) ===")
        sys.exit(1)
    print("=== alles in orde ===")


if __name__ == "__main__":
    main()
