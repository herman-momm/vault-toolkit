#!/bin/bash
# Tests voor de verouderingscheck op geplande taken in vault-status.py.
#
# Waarom die check bestaat: launchd haalt een gemiste slag in als de Mac uit
# stond, maar een run die *faalt* meldde niets. Op de dag dat deze check werd
# toegevoegd bleek de websitecontrole al 37 uur stil te staan (laatste exit
# status 78, en in het log een Claude-weeklimiet) — zonder dat iets dat ergens
# liet zien.
#
# De tests zetten de mtime van wegwerplogbestanden en kijken wat status.md
# daarvan zegt. De logpaden van de twee gedeelde taken worden omgeleid met de
# override-env-vars van vaultenv (VAULT_<NAAM>_ENV), zodat de echte
# ~/.config-logs niet worden aangeraakt.
set -u

HIER="$(cd "$(dirname "$0")" && pwd)"
TOOLKIT="$(dirname "$HIER")"
STATUS="$TOOLKIT/scripts/vault-status.py"
SANDBOX=$(mktemp -d)
fails=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails+1)); }

VAULT=$(python3 -c "
import sys; sys.path.insert(0, '$HIER')
import fixture_vault; print(fixture_vault.bouw())
")

SYNC_LOG="$SANDBOX/daily-github-sync.log"
BOOK_LOG="$SANDBOX/daily-book-orchestrator.log"
mkdir -p "$SANDBOX/home"
printf 'fixture\n' > "$SYNC_LOG"
printf 'fixture\n' > "$BOOK_LOG"

ouder() { # $1 = bestand, $2 = uren oud
  local stamp
  stamp=$(python3 -c "
import datetime, sys
t = datetime.datetime.now() - datetime.timedelta(hours=float(sys.argv[1]))
print(t.strftime('%Y%m%d%H%M.%S'))
" "$2")
  touch -t "$stamp" "$1"
}

# HOME wordt omgelegd naar de sandbox. Zonder dat valt vaultenv bij een
# ontbrekend override-pad terug op ~/.config/histvault/<log>, en dat bestaat op
# deze machine écht — waardoor test 4 ("geen logbestand") het echte log vond en
# een verse run rapporteerde. Dat was een fout in de test, niet in het script:
# de terugvalketen hoort juist zo te werken.
draai_status() {
  ( cd "$VAULT" \
    && HOME="$SANDBOX/home" \
       VAULT_DAILY_GITHUB_SYNC_ENV="$SYNC_LOG" \
       VAULT_DAILY_BOOK_ORCHESTRATOR_ENV="$BOOK_LOG" \
       python3 "$STATUS" --vault "$VAULT" 2>&1 )
}

status_md() { cat "$VAULT/wiki/status.md"; }

# --- 1. beide logs vers: alles groen ---------------------------------------
ouder "$SYNC_LOG" 2
ouder "$BOOK_LOG" 5
uit=$(draai_status)
case "$uit" in
  *"0 stille taken"*) ok "1a samenvatting meldt 0 stille taken" ;;
  *) bad "1a samenvatting: $uit" ;;
esac
case "$(status_md)" in
  *"🟢 **Automatisering** — alle geplande taken hebben recent gedraaid"*)
    ok "1b statusregel groen" ;;
  *) bad "1b statusregel niet groen" ;;
esac
case "$(status_md)" in
  *"| Git-sync alle repo's | 03:00 |"*) ok "1c sync-taak in de tabel" ;;
  *) bad "1c sync-taak ontbreekt in de tabel" ;;
esac

# --- 2. sync-log te oud: rood, met actieregel ------------------------------
ouder "$SYNC_LOG" 40
uit=$(draai_status)
case "$uit" in
  *"1 stille taken"*) ok "2a samenvatting meldt 1 stille taak" ;;
  *) bad "2a samenvatting: $uit" ;;
esac
md=$(status_md)
case "$md" in
  *"🔴 **Automatisering** — 1 geplande taak heeft te lang niet gedraaid"*)
    ok "2b statusregel rood, enkelvoud" ;;
  *) bad "2b statusregel/enkelvoud fout" ;;
esac
case "$md" in
  *"**Git-sync alle repo's** draaide 40 uur niet"*) ok "2c noemt taak en leeftijd" ;;
  *) bad "2c actieregel ontbreekt" ;;
esac
case "$md" in
  *"$SYNC_LOG"*) ok "2d noemt het logbestand" ;;
  *) bad "2d logpad niet genoemd" ;;
esac

# --- 3. beide te oud: meervoud --------------------------------------------
ouder "$BOOK_LOG" 100
uit=$(draai_status)
case "$uit" in
  *"2 stille taken"*) ok "3a samenvatting meldt 2 stille taken" ;;
  *) bad "3a samenvatting: $uit" ;;
esac
case "$(status_md)" in
  *"🔴 **Automatisering** — 2 geplande taken hebben te lang niet gedraaid"*)
    ok "3b statusregel rood, meervoud" ;;
  *) bad "3b meervoud fout" ;;
esac
case "$(status_md)" in
  *"draaide 4 dagen niet"*) ok "3c leeftijd in dagen bij >48 uur" ;;
  *) bad "3c dagen-notatie ontbreekt" ;;
esac

# --- 4. ontbrekend logbestand: rood, andere melding -----------------------
ouder "$SYNC_LOG" 2
ouder "$BOOK_LOG" 2
rm -f "$BOOK_LOG"
draai_status >/dev/null
case "$(status_md)" in
  *"| Boek-orchestrator | 06:00 | *geen logbestand gevonden* | — | 🔴 |"*)
    ok "4a ontbrekend log als rood gemeld" ;;
  *) bad "4a ontbrekend log niet gemeld" ;;
esac
case "$(status_md)" in
  *"heeft geen logbestand"*) ok "4b actieregel voor ontbrekend log" ;;
  *) bad "4b actieregel ontbreekt" ;;
esac
printf 'fixture\n' > "$BOOK_LOG"; ouder "$BOOK_LOG" 2

# --- 5. vaultlog: round-robin is informatief, niet alarmerend -------------
mkdir -p "$VAULT/scripts"
printf 'fixture\n' > "$VAULT/scripts/daily-book.log"
ouder "$VAULT/scripts/daily-book.log" 200
uit=$(draai_status)
case "$uit" in
  *"0 stille taken"*) ok "5a oud vaultlog veroorzaakt geen alarm" ;;
  *) bad "5a vaultlog slaat onterecht alarm: $uit" ;;
esac
case "$(status_md)" in
  *"| Deze vault aan de beurt (boek) | round-robin |"*|*"ℹ️"*)
    ok "5b vaultlog als info gemeld" ;;
  *) bad "5b vaultlog ontbreekt" ;;
esac

# --- 6. ontbrekende websitecontrole telt niet mee (loopt niet in elke vault)
case "$(status_md)" in
  *"Websitecontrole"*) bad "6a websitecontrole gemeld zonder logbestand" ;;
  *) ok "6a websitecontrole stil genegeerd zonder logbestand" ;;
esac
printf 'fixture\n' > "$VAULT/scripts/website-check.log"
ouder "$VAULT/scripts/website-check.log" 50
uit=$(draai_status)
case "$uit" in
  *"1 stille taken"*) ok "6b mét logbestand telt een oude run wél mee" ;;
  *) bad "6b websitecontrole niet meegeteld: $uit" ;;
esac

python3 -c "
import sys; sys.path.insert(0, '$HIER')
import fixture_vault; fixture_vault.ruim_op('$VAULT')
"
rm -rf "$SANDBOX"
echo
if [ "$fails" -gt 0 ]; then echo "=== $fails TEST(S) GEFAALD ==="; exit 1; fi
echo "=== alle automatiseringstests geslaagd ==="
