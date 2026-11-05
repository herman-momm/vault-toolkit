#!/bin/bash
# Tests voor de store-ID-controle in sync-blob-inbox.py.
#
# Waarom deze controle bestaat: drie vaults hebben elk hun eigen Vercel
# Blob-store. Draait de sync in de ene vault met het token van een andere, dan
# meldt hij zonder controle "inbox is leeg" — of, erger, hij zet de bestanden
# van de ene vault in de raw/ van een andere. De verwachte store-ID staat
# daarom per vault in scripts/blob-store-id.txt, in git, want `vercel env pull`
# herschrijft web/.env.local en zou een store-ID die alleen daar staat
# ongemerkt wissen.
#
# Deze tests gebruiken uitsluitend synthetische vaults met neptokens, dus er
# komt geen echte credential aan te pas. Let op het verschil tussen de twee
# soorten gevallen:
#
#   - Mismatch, onbekende tokenvorm, ontbrekend bestand: het script stopt vóór
#     elke API-aanroep. Volledig offline, en test 2f controleert expliciet dat
#     er inderdaad niets is aangeroepen.
#   - Token dat bij de vastgelegde store hoort (1a, 1b, 5a, 7a): de controle
#     slaagt en het script loopt dóór naar de Vercel API, die met het neptoken
#     voorspelbaar faalt ("store_not_found"). Dat is onschuldig — geen
#     authenticatie, geen data — en de assertions kijken alleen naar de
#     store-ID-regel, die het script vóór die aanroep print. Zonder netwerk
#     faalt curl in plaats van de API en blijven dezelfde assertions gelden,
#     dus de suite werkt ook offline.
#
# Wat hierdoor níet getoetst wordt, is het volledige pad met een echt token:
# downloaden en daarna uit de inbox verwijderen. Daar is een echte store voor
# nodig.
set -u

TOOLKIT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$TOOLKIT/scripts/sync-blob-inbox.py"
SANDBOX=$(mktemp -d)
fails=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails+1)); }

# Twee verschillende, verzonnen stores. De tokenvorm is wat het script parseert:
# vercel_blob_rw_<storeId>_<secret>.
STORE_A="store_AAAAAAAAAAAAAAAA"
STORE_B="store_BBBBBBBBBBBBBBBB"
TOKEN_A="vercel_blob_rw_AAAAAAAAAAAAAAAA_neptokenzondernut"
TOKEN_B="vercel_blob_rw_BBBBBBBBBBBBBBBB_neptokenzondernut"

mkvault() { # $1 = naam, $2 = store-ID in scripts/blob-store-id.txt (leeg = geen bestand)
  local v="$SANDBOX/$1"
  mkdir -p "$v/raw" "$v/scripts" "$v/web"
  if [ -n "${2:-}" ]; then
    printf '# fixture\n%s\n' "$2" > "$v/scripts/blob-store-id.txt"
  fi
}

run() { # $1 = vaultnaam, rest = extra argumenten
  local v="$SANDBOX/$1"; shift
  ( cd "$v" && VAULT_ROOT="$v" python3 "$SCRIPT" --dry-run "$@" 2>&1 )
}

# --- 1. token hoort bij de vastgelegde store: check slaagt, bron gemeld ------
mkvault alpha "$STORE_A"
uit=$(run alpha --token "$TOKEN_A")
case "$uit" in
  *"Store-ID: $STORE_A (gecontroleerd tegen scripts/blob-store-id.txt)"*)
    ok "1a meldt store-ID en de git-bron" ;;
  *) bad "1a melding ontbreekt: $uit" ;;
esac
case "$uit" in
  *"Let op"*) bad "1b onverwachte waarschuwing: $uit" ;;
  *) ok "1b geen waarschuwing" ;;
esac

# --- 2. token van een andere store: afbreken vóór elke API-aanroep -----------
uit=$(run alpha --token "$TOKEN_B"); rc=$?
[ "$rc" = "1" ] && ok "2a exitcode 1 (afgebroken)" || bad "2a exitcode $rc, wilde 1"
case "$uit" in
  *"Store-ID mismatch"*) ok "2b meldt de mismatch" ;;
  *) bad "2b geen mismatchmelding: $uit" ;;
esac
case "$uit" in
  *"$STORE_B"*) ok "2c noemt de store uit het token" ;;
  *) bad "2c token-store niet genoemd" ;;
esac
case "$uit" in
  *"verwacht $STORE_A"*) ok "2d noemt de verwachte store" ;;
  *) bad "2d verwachte store niet genoemd" ;;
esac
case "$uit" in
  *"pas dan scripts/blob-store-id.txt aan"*) ok "2e noemt het te bewerken bestand" ;;
  *) bad "2e oplossing niet genoemd" ;;
esac
case "$uit" in
  *"Inbox is leeg"*|*"Gedownload"*|*"API-fout"*)
    bad "2f heeft tóch de API aangeroepen: $uit" ;;
  *) ok "2f geen API-aanroep gedaan" ;;
esac

# --- 3. onbekende tokenvorm: waarschuwen, niet onterecht afbreken -----------
uit=$(run alpha --token "iets-wat-geen-blob-token-is")
case "$uit" in
  *"tokenvorm niet herkend"*) ok "3a waarschuwt over de tokenvorm" ;;
  *) bad "3a geen waarschuwing: $uit" ;;
esac
case "$uit" in
  *"Store-ID mismatch"*) bad "3b onterechte mismatch" ;;
  *) ok "3b geen onterechte mismatch" ;;
esac

# --- 4. geen blob-store-id.txt: waarschuwen dat er niet gecontroleerd is ----
mkvault beta ""
uit=$(run beta --token "$TOKEN_A")
case "$uit" in
  *"geen verwachte store-ID bekend"*) ok "4a waarschuwt bij ontbrekend bestand" ;;
  *) bad "4a geen waarschuwing: $uit" ;;
esac
case "$uit" in
  *"Zet hem in scripts/blob-store-id.txt"*) ok "4b noemt het aan te maken bestand" ;;
  *) bad "4b bestand niet genoemd" ;;
esac

# --- 5. commentaar en lege regels worden overgeslagen -----------------------
mkvault gamma ""
printf '\n# alleen commentaar\n\n   \n%s   \n' "$STORE_A" \
  > "$SANDBOX/gamma/scripts/blob-store-id.txt"
uit=$(run gamma --token "$TOKEN_A")
case "$uit" in
  *"Store-ID: $STORE_A"*) ok "5a commentaar/witruimte genegeerd" ;;
  *) bad "5a parsefout: $uit" ;;
esac

# --- 6. de oude env-var-route blijft werken als het bestand ontbreekt ------
uit=$( cd "$SANDBOX/beta" && VAULT_ROOT="$SANDBOX/beta" BLOB_STORE_ID="$STORE_B" \
        python3 "$SCRIPT" --dry-run --token "$TOKEN_A" 2>&1 )
case "$uit" in
  *"Store-ID mismatch"*) ok "6a env-var wordt nog steeds gebruikt" ;;
  *) bad "6a env-var-route werkt niet meer: $uit" ;;
esac

# --- 7. --store-id op de opdrachtregel gaat vóór het bestand ---------------
uit=$(run alpha --token "$TOKEN_B" --store-id "$STORE_B")
case "$uit" in
  *"Store-ID: $STORE_B (gecontroleerd tegen command line)"*)
    ok "7a --store-id overschrijft het bestand" ;;
  *) bad "7a override werkt niet: $uit" ;;
esac

rm -rf "$SANDBOX"
echo
if [ "$fails" -gt 0 ]; then echo "=== $fails TEST(S) GEFAALD ==="; exit 1; fi
echo "=== alle store-ID-tests geslaagd ==="
