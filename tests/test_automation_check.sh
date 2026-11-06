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
  *"0 taken stil of mislukt"*) ok "1a samenvatting meldt 0 problemen" ;;
  *) bad "1a samenvatting: $uit" ;;
esac
case "$(status_md)" in
  *"🟢 **Automatisering** — alle geplande taken hebben recent gedraaid en zijn geslaagd"*)
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
  *"1 taken stil of mislukt"*) ok "2a samenvatting meldt 1 probleem" ;;
  *) bad "2a samenvatting: $uit" ;;
esac
md=$(status_md)
case "$md" in
  *"🔴 **Automatisering** — 1 geplande taak draaide te lang niet of is mislukt"*)
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
  *"2 taken stil of mislukt"*) ok "3a samenvatting meldt 2 problemen" ;;
  *) bad "3a samenvatting: $uit" ;;
esac
case "$(status_md)" in
  *"🔴 **Automatisering** — 2 geplande taken draaiden te lang niet of zijn mislukt"*)
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
  *"| Boek-orchestrator | 06:00 | *geen logbestand gevonden* | — | — | 🔴 |"*)
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
  *"0 taken stil of mislukt"*) ok "5a oud vaultlog veroorzaakt geen alarm" ;;
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
  *"1 taken stil of mislukt"*) ok "6b mét logbestand telt een oude run wél mee" ;;
  *) bad "6b websitecontrole niet meegeteld: $uit" ;;
esac

# --- 7. verse run die MISLUKT is: rood, met de logregel erbij --------------
# De blinde vlek die dit dashboard op 6 nov 2026 had: de sync weigerde twee
# repo's, schreef dat in zijn log, en de taak bleef groen omdat de mtime vers was.
rm -f "$VAULT/scripts/website-check.log"
cat > "$SYNC_LOG" <<'LOG'
[2026-11-06 07:32:01 CET] START dagelijkse GitHub-sync (launchd 03:00)
[2026-11-06 07:32:36 CET] [vault-toolkit] !! GEWEIGERD: de commit zou een secret bevatten — repo overgeslagen:
[2026-11-06 07:32:39 CET] EINDE sync: 3 geslaagd, 2 mislukt, 2 repo(s) met een nieuwe auto-commit, 0 inbox-sync(s) mislukt.
LOG
ouder "$SYNC_LOG" 1
uit=$(draai_status)
case "$uit" in
  *"1 taken stil of mislukt"*) ok "7a mislukte verse run telt mee" ;;
  *) bad "7a mislukte run niet geteld: $uit" ;;
esac
md=$(status_md)
case "$md" in
  *"| Git-sync alle repo's | 03:00 |"*"| **mislukt** | 🔴 |"*)
    ok "7b tabel meldt de uitkomst als mislukt" ;;
  *) bad "7b uitkomstkolom niet rood/mislukt" ;;
esac
case "$md" in
  *"maar die run is mislukt"*) ok "7c actieregel noemt de mislukking" ;;
  *) bad "7c actieregel ontbreekt" ;;
esac
case "$md" in
  *"3 geslaagd, 2 mislukt"*) ok "7d de logregel staat in de actieregel" ;;
  *) bad "7d logregel niet geciteerd" ;;
esac

# --- 8. eindregel met 0 mislukt blijft groen (geen valse positief) ---------
# "0 inbox-sync(s) mislukt" bevat het woord; dat mag geen alarm geven.
cat > "$SYNC_LOG" <<'LOG'
[2026-11-06 03:00:01 CET] START dagelijkse GitHub-sync (launchd 03:00)
[2026-11-06 03:00:20 CET] [ClaudeVault] Push geslaagd.
[2026-11-06 03:00:39 CET] EINDE sync: 5 geslaagd, 0 mislukt, 1 repo(s) met een nieuwe auto-commit, 0 inbox-sync(s) mislukt.
LOG
ouder "$SYNC_LOG" 1
uit=$(draai_status)
case "$uit" in
  *"0 taken stil of mislukt"*) ok "8a 0 mislukt geeft geen alarm" ;;
  *) bad "8a valse positief op het woord mislukt: $uit" ;;
esac

# --- 9. niet-nul exitcode in een wrapper-log: rood ------------------------
cat > "$BOOK_LOG" <<'LOG'
[2026-11-06 06:00:05 CET] start — vorige index: 0
[2026-11-06 06:00:05 CET] gekozen vault: ClaudeVault  (index 2)
[2026-11-06 06:03:58 CET] ClaudeVault klaar (wrapper exit=78)
LOG
ouder "$BOOK_LOG" 1
uit=$(draai_status)
case "$uit" in
  *"1 taken stil of mislukt"*) ok "9a exit=78 wordt opgemerkt" ;;
  *) bad "9a niet-nul exitcode gemist: $uit" ;;
esac
case "$(status_md)" in
  *"wrapper exit=78"*) ok "9b de exit-regel staat erbij" ;;
  *) bad "9b exit-regel niet geciteerd" ;;
esac
printf 'fixture\n' > "$BOOK_LOG"; ouder "$BOOK_LOG" 2

# --- 10. alleen de LAATSTE run telt --------------------------------------
# Een oudere mislukte run mag een geslaagde laatste run niet rood maken.
cat > "$SYNC_LOG" <<'LOG'
[2026-11-05 03:00:01 CET] START dagelijkse GitHub-sync (launchd 03:00)
[2026-11-05 03:00:39 CET] EINDE sync: 1 geslaagd, 4 mislukt, 0 repo(s) met een nieuwe auto-commit.
[2026-11-06 03:00:01 CET] START dagelijkse GitHub-sync (launchd 03:00)
[2026-11-06 03:00:39 CET] EINDE sync: 5 geslaagd, 0 mislukt, 0 repo(s) met een nieuwe auto-commit.
LOG
ouder "$SYNC_LOG" 1
uit=$(draai_status)
case "$uit" in
  *"0 taken stil of mislukt"*) ok "10a oude mislukking blijft buiten beschouwing" ;;
  *) bad "10a oude mislukking telt onterecht mee: $uit" ;;
esac

# --- 10b. geklaag onderweg, maar de run slaagt: groen -------------------
# Echte regressie uit HistVault: de boekentaak logt "!! CLI Subscription Limit"
# als de Claude CLI aan zijn limiet zit, draait dan door op de Gemini-fallback
# en eindigt met exit=0. Op de !!-regel alleen stond hij ten onrechte rood.
cat > "$BOOK_LOG" <<'LOG'
===== START 2026-11-06 06:00:56 CET =====
!! CLI Subscription Limit bereikt op OAuth token.
Terugvallen op daily-book-fallback.py...
=== GESLAAGD! Boek 'Testboek' is verwerkt. ===
----- Dagelijkse boeksamenvatting einde 06:03:58 exit=0 -----
===== EINDE 2026-11-06 06:03:58 =====
LOG
ouder "$BOOK_LOG" 1
uit=$(draai_status)
case "$uit" in
  *"0 taken stil of mislukt"*) ok "10b handled !! met exit=0 blijft groen" ;;
  *) bad "10b valse positief op een !!-regel: $uit" ;;
esac
# Zelfde log, maar nu faalt het slot: dan moet hij wél rood zijn.
cat > "$BOOK_LOG" <<'LOG'
===== START 2026-11-06 06:00:56 CET =====
!! CLI Subscription Limit bereikt op OAuth token.
----- Dagelijkse boeksamenvatting einde 06:03:58 exit=1 -----
LOG
ouder "$BOOK_LOG" 1
uit=$(draai_status)
case "$uit" in
  *"1 taken stil of mislukt"*) ok "10c zelfde log met exit=1 is wél rood" ;;
  *) bad "10c mislukt slot niet opgemerkt: $uit" ;;
esac
# Afgebroken run zonder slotwoord: dan is de schreeuwmarker het enige signaal.
cat > "$BOOK_LOG" <<'LOG'
===== START 2026-11-06 06:00:56 CET =====
Traceback (most recent call last):
  File "daily-book-fallback.py", line 1, in <module>
LOG
ouder "$BOOK_LOG" 1
uit=$(draai_status)
case "$uit" in
  *"1 taken stil of mislukt"*) ok "10d afgebroken run zonder slot is rood" ;;
  *) bad "10d afgebroken run gemist: $uit" ;;
esac
printf 'fixture\n' > "$BOOK_LOG"; ouder "$BOOK_LOG" 2

# --- 11. te oud én mislukt: één probleem, en 'te lang stil' weegt zwaarder -
cat > "$SYNC_LOG" <<'LOG'
[2026-11-01 03:00:01 CET] START dagelijkse GitHub-sync (launchd 03:00)
[2026-11-01 03:00:39 CET] EINDE sync: 1 geslaagd, 4 mislukt, 0 repo(s) met een nieuwe auto-commit.
LOG
ouder "$SYNC_LOG" 60
uit=$(draai_status)
case "$uit" in
  *"1 taken stil of mislukt"*) ok "11a stil én mislukt telt één keer" ;;
  *) bad "11a dubbel geteld: $uit" ;;
esac
case "$(status_md)" in
  *"| te lang stil | 🔴 |"*) ok "11b leeftijd weegt zwaarder dan de uitkomst" ;;
  *) bad "11b verkeerde uitkomstkolom" ;;
esac

python3 -c "
import sys; sys.path.insert(0, '$HIER')
import fixture_vault; fixture_vault.ruim_op('$VAULT')
"
rm -rf "$SANDBOX"
echo
if [ "$fails" -gt 0 ]; then echo "=== $fails TEST(S) GEFAALD ==="; exit 1; fi
echo "=== alle automatiseringstests geslaagd ==="
