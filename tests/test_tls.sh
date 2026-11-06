#!/bin/bash
# Bewaakt dat de toolkit-scripts https mét certificaatcontrole doen.
#
# Waarom: tot 6 nov 2026 gebruikten capacities-push.py, fill-capacities-photos.py,
# gemini-book-runner.py en gemini-fallback.py `ssl._create_unverified_context()`,
# de laatste twee als stille terugval bij élke uitzondering. Bij elke API-aanroep
# ging daardoor een bearer-token de deur uit zonder dat het certificaat van de
# tegenpartij werd gecontroleerd. De oorzaak was een ontbrekende CA-bundel in de
# python.org-build op deze Mac, niet iets aan de API's — en de oplossing is die
# bundel opzoeken (`vaultenv.tls_context()`), niet de controle uitzetten.
#
# Deze suite is bewust een *verbod*: hij toetst dat de patronen die de controle
# omzeilen niet terugkomen. Dat is het soort regressie dat je niet opmerkt, want
# alles blijft werken — alleen onveilig.
#
# Geen netwerk nodig: de contextcheck kijkt naar de instellingen van het
# SSLContext-object, niet naar een verbinding.
set -u

HIER="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HIER/.." && pwd)"
SCRIPTS="$ROOT/scripts"

fouten=0
ok()   { echo "ok   $1"; }
faal() { echo "FAAL $1"; fouten=$((fouten + 1)); }

echo "--- verboden patronen in scripts/ ---"
# De scan tokeniseert elk bestand in plaats van te grepen. Dat is nodig, niet
# netter: `vaultenv.py` en `capacities-check.py` noemen deze namen in hun
# docstring om uit te leggen waarom ze er niet meer staan, en een grep ziet geen
# verschil tussen die uitleg en een echte aanroep.
uit=$(cd "$SCRIPTS" && python3 - <<'PY' 2>&1
import io, pathlib, sys, token, tokenize

VERBODEN_NAMEN = {"_create_unverified_context", "CERT_NONE"}
gevonden = []

for pad in sorted(pathlib.Path(".").glob("*.py")):
    try:
        with open(pad, "rb") as fh:
            toks = list(tokenize.tokenize(fh.readline))
    except (SyntaxError, tokenize.TokenError) as e:
        print(f"ONLEESBAAR {pad}: {e}")
        continue
    for i, t in enumerate(toks):
        if t.type != token.NAME or t.string not in VERBODEN_NAMEN:
            continue
        gevonden.append(f"{pad}:{t.start[0]}: {t.string}")
    # verify=False / check_hostname=False als echte keyword-toekenning
    for i in range(len(toks) - 2):
        a, b, c = toks[i], toks[i + 1], toks[i + 2]
        if (a.type == token.NAME and a.string in ("verify", "check_hostname")
                and b.type == token.OP and b.string == "="
                and c.type == token.NAME and c.string == "False"):
            gevonden.append(f"{pad}:{a.start[0]}: {a.string}=False")

if gevonden:
    print("TREFFERS")
    for g in gevonden:
        print(g)
else:
    print("SCHOON")
PY
)
if echo "$uit" | grep -q "^SCHOON$"; then
  ok "geen code die certificaatcontrole omzeilt (getokeniseerd, docstrings uitgezonderd)"
else
  faal "code die certificaatcontrole omzeilt is terug:"
  echo "$uit" | grep -v '^TREFFERS$' | sed 's/^/       /'
fi

echo
echo "--- vaultenv.tls_context() ---"
uit=$(cd "$SCRIPTS" && python3 - <<'PY' 2>&1
import ssl, sys
sys.path.insert(0, ".")
import vaultenv

ctx = vaultenv.tls_context()
# .name, want sinds Python 3.11 print een IntEnum als het getal.
print("verify_mode:", ctx.verify_mode.name)
print("check_hostname:", ctx.check_hostname)
pad, bron = vaultenv.ca_bundle()
print("bundel:", bron)
stats = ctx.cert_store_stats()
print("x509_ca:", stats.get("x509_ca", 0))
PY
)
echo "$uit" | sed 's/^/     /'

echo "$uit" | grep -q "verify_mode: CERT_REQUIRED" \
  && ok "verify_mode is CERT_REQUIRED" \
  || faal "verify_mode is niet CERT_REQUIRED"
echo "$uit" | grep -q "check_hostname: True" \
  && ok "check_hostname staat aan" \
  || faal "check_hostname staat uit"
aantal=$(echo "$uit" | sed -n 's/^x509_ca: //p')
if [ -n "$aantal" ] && [ "$aantal" -gt 0 ]; then
  ok "de context heeft $aantal CA-certificaten geladen"
else
  faal "geen CA-certificaten geladen — verificatie zou altijd falen"
fi
echo "$uit" | grep -q "bundel: geen bundel gevonden" \
  && faal "vaultenv vindt geen CA-bundel op deze machine" \
  || ok "vaultenv vindt een CA-bundel"

echo
echo "--- gebruiken de credentialscripts die context? ---"
for f in capacities-push.py fill-capacities-photos.py gemini-book-runner.py gemini-fallback.py capacities-check.py; do
  if [ ! -f "$SCRIPTS/$f" ]; then
    faal "$f bestaat niet"
    continue
  fi
  grep -q "vaultenv.tls_context()" "$SCRIPTS/$f" \
    && ok "$f gebruikt vaultenv.tls_context()" \
    || faal "$f gebruikt vaultenv.tls_context() niet"
  python3 -m py_compile "$SCRIPTS/$f" 2>/dev/null \
    && ok "$f compileert" \
    || faal "$f compileert niet"
done

echo
if [ "$fouten" -gt 0 ]; then
  echo "=== $fouten controle(s) gefaald ==="
  exit 1
fi
echo "=== alle TLS-controles geslaagd ==="
