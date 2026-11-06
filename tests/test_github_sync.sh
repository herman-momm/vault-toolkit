#!/bin/bash
# Tests voor daily-github-sync.sh (launchd, 03:00) — met echte git-repo's in
# een wegwerpmap, inclusief bare origins, zodat push/pull/rebase echt lopen.
#
# Waarom deze tests bestaan: de sync pushte eerder alleen wat al gecommit was,
# waardoor sessiewerk nooit op GitHub kwam (bij het schrijven hiervan: 123
# ongecommitte bestanden over vier repo's). De auto-commit die dat oplost raakt
# alles in alle repo's, dus de twee gevaarlijke kanten worden hier getoetst:
# dat er niets ongemerkt blijft staan, en dat een repo met een secret in de
# wijziging wordt geweigerd in plaats van gecommit.
#
# Alles blijft lokaal — geen netwerk, geen echte remote.
set -u

TOOLKIT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$TOOLKIT/scripts/daily-github-sync.sh"
SANDBOX=$(mktemp -d)
WS="$SANDBOX/workspace"
LOG="$SANDBOX/sync.log"
fails=0

ok()   { echo "ok   $1"; }
bad()  { echo "FAIL $1"; fails=$((fails+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (kreeg '$2', wilde '$3')"; fi; }

mkrepo() { # $1 = naam
  local name="$1"
  git init -q --bare "$SANDBOX/origin-$name.git"
  git -c init.defaultBranch=main clone -q "$SANDBOX/origin-$name.git" "$WS/$name" 2>/dev/null
  git -C "$WS/$name" config user.email test@example.com
  git -C "$WS/$name" config user.name Test
  git -C "$WS/$name" checkout -q -b main 2>/dev/null || true
  printf 'start\n' > "$WS/$name/README.md"
  printf '.env*\n' > "$WS/$name/.gitignore"
  git -C "$WS/$name" add -A && git -C "$WS/$name" commit -q -m "init"
  git -C "$WS/$name" push -q -u origin main 2>/dev/null
}

# Stap 0 (de upload-inbox) staat hier UIT. Zonder die rem zouden deze tests de
# echte blob-store van ClaudeVault/HistVault/GroeneVault aanroepen — netwerk, en
# bestanden in de raw/ van een echte vault. De inbox-stap krijgt zijn eigen
# runner met een stub, hieronder.
run() {
  local dry="${2:-0}"
  VAULT_WORKSPACE="$WS" VAULT_SYNC_REPOS="$1" VAULT_SYNC_LOG="$LOG" \
    VAULT_SYNC_DRY_RUN="$dry" VAULT_SYNC_SKIP_INBOX=1 \
    bash "$SCRIPT" >/dev/null 2>&1
  echo $?
}

# Zelfde runner, maar met stap 0 aan voor de opgegeven vaults. VAULT_SYNC_PYTHON
# staat op `bash`, zodat de stub hieronder geen python nodig heeft.
run_inbox() { # $1 = repolijst, $2 = inboxvaults, $3 = dry-run (optioneel)
  VAULT_WORKSPACE="$WS" VAULT_SYNC_REPOS="$1" VAULT_SYNC_LOG="$LOG" \
    VAULT_SYNC_DRY_RUN="${3:-0}" VAULT_SYNC_INBOX_VAULTS="$2" \
    VAULT_SYNC_PYTHON=bash bash "$SCRIPT" >/dev/null 2>&1
  echo $?
}

# Nep-sync-blob-inbox.py: praat als het echte script, raakt geen netwerk.
# $1 = vault, $2 = exitcode, $3 = bestand dat het "ophaalt" (leeg = inbox leeg)
mkinbox() {
  mkdir -p "$WS/$1/scripts" "$WS/$1/raw"
  {
    echo 'echo "Token uit: teststub"'
    echo 'if [ "$1" = "--dry-run" ]; then'
    echo '  echo "--dry-run: niets gedownload of verwijderd."'
    echo "  exit $2"
    echo 'fi'
    if [ -n "${3:-}" ]; then
      echo 'echo inhoud > "$VAULT_ROOT/raw/'"$3"'"'
      echo 'echo "Klaar. Nieuwe bestanden staan in raw/: '"$3"'"'
    else
      echo 'echo "Inbox is leeg — niets te synchroniseren."'
    fi
    echo "exit $2"
  } > "$WS/$1/scripts/sync-blob-inbox.py"
}

mkdir -p "$WS"
echo "sandbox: $SANDBOX"
echo

# --- 1. ongecommit werk wordt gecommit en gepusht ---
mkrepo alpha
mkdir -p "$WS/alpha/wiki" "$WS/alpha/exports"
echo "nieuwe pagina" > "$WS/alpha/wiki/pagina.md"
echo "nog een"       > "$WS/alpha/wiki/pagina2.md"
echo "export"        > "$WS/alpha/exports/doc.md"
rc=$(run alpha)
check "1a exitcode 0"            "$rc" "0"
check "1b werkmap schoon na run" "$(git -C "$WS/alpha" status --porcelain | wc -l | tr -d ' ')" "0"
check "1c commit gepusht"        "$(git -C "$WS/alpha" log origin/main..HEAD --oneline | wc -l | tr -d ' ')" "0"
msg=$(git -C "$WS/alpha" log -1 --format=%s)
case "$msg" in
  "auto: dagelijkse sync "*"(3 bestand(en))") ok "1d commitboodschap: $msg" ;;
  *) bad "1d commitboodschap onverwacht: $msg" ;;
esac
body=$(git -C "$WS/alpha" log -1 --format=%b)
case "$body" in
  *"wiki/: 2"*) ok "1e samenvatting per map in body" ;;
  *) bad "1e samenvatting ontbreekt: $body" ;;
esac

# --- 2. tweede run op een schone repo doet niets ---
rc=$(run alpha)
check "2a exitcode 0"              "$rc" "0"
check "2b geen nieuwe commit"      "$(git -C "$WS/alpha" log --oneline | wc -l | tr -d ' ')" "2"
grep -q "Reeds up-to-date" "$LOG" && ok "2c meldt up-to-date" || bad "2c melding ontbreekt"

# --- 3. secret-rem: niet-genegeerd .env-bestand blokkeert de repo ---
mkrepo beta
printf '# geen ignore\n' > "$WS/beta/.gitignore"
git -C "$WS/beta" add -A && git -C "$WS/beta" commit -q -m "ignore leeg"
echo "WP_APP_PASSWORD=geheim" > "$WS/beta/.env.local"
echo "gewone wijziging"       > "$WS/beta/wiki.md"
rc=$(run beta)
check "3a exitcode 1 (geweigerd)" "$rc" "1"
check "3b niets gecommit"         "$(git -C "$WS/beta" log --oneline | wc -l | tr -d ' ')" "2"
check "3c niets meer staged"      "$(git -C "$WS/beta" diff --cached --name-only | wc -l | tr -d ' ')" "0"
check "3d bestanden nog aanwezig" "$(ls "$WS/beta/.env.local" >/dev/null 2>&1 && echo ja)" "ja"
grep -q "GEWEIGERD" "$LOG" && ok "3e waarschuwing in het log" || bad "3e waarschuwing ontbreekt"

# --- 3b. secret-rem op INHOUD: gewone bestandsnaam, sleutel in de tekst ---
#
# De fixture-sleutel wordt hier SAMENGESTELD en staat niet voluit in dit
# bestand. Twee redenen, beide op 6 nov 2026 pijnlijk duidelijk geworden:
#   1. Een literal in de sleutelvorm laat dit testbestand zélf de secret-rem
#      matchen, waardoor de nachtsync de hele vault-toolkit-repo overslaat
#      zodra iemand deze tests aanraakt.
#   2. Wat hier als "fixture" stond, was de échte Capacities-sleutel. Die is
#      uit de (nog ongepushte) historie van dit repo geredigeerd; `cap-api-`
#      gevolgd door niets-in-de-tekenklasse matcht de rem niet.
# De sandbox-kopie hieronder krijgt wél een sleutel die de rem moet vangen —
# daar hoort hij, en die map is een wegwerpmap.
mkrepo zeta
fixture_key="cap-api-$(printf 'X%.0s' $(seq 1 40))"
cat > "$WS/zeta/experiment.py" <<PY
import urllib.request
token = '$fixture_key'
PY
echo "gewone pagina" > "$WS/zeta/wiki.md"
rc=$(run zeta)
check "3f exitcode 1 (geweigerd op inhoud)" "$rc" "1"
check "3g niets gecommit"                   "$(git -C "$WS/zeta" log --oneline | wc -l | tr -d ' ')" "1"
check "3h niets meer staged"                "$(git -C "$WS/zeta" diff --cached --name-only | wc -l | tr -d ' ')" "0"
grep -q "experiment.py" "$LOG" && ok "3i het bestand is bij naam gemeld" || bad "3i bestand niet gemeld"

# --- 3c. gewone vaultinhoud wordt NIET geweigerd (geen valse positieven) ---
mkrepo eta
mkdir -p "$WS/eta/wiki"
printf '# Pagina\n\nOver sk-eletten en tokenisatie in de taalkunde.\n\n| a | b |\n|---|---|\n' > "$WS/eta/wiki/taal.md"
printf 'Een notitie over API-keys in het algemeen, zonder sleutel.\n' > "$WS/eta/wiki/notitie.md"
rc=$(run eta)
check "3j exitcode 0 (geen valse weigering)" "$rc" "0"
check "3k wel gecommit"                      "$(git -C "$WS/eta" status --porcelain | wc -l | tr -d ' ')" "0"

# --- 4. uiteengelopen branch: rebase + push ---
mkrepo gamma
git clone -q "$SANDBOX/origin-gamma.git" "$SANDBOX/other-gamma"
git -C "$SANDBOX/other-gamma" config user.email other@example.com
git -C "$SANDBOX/other-gamma" config user.name Other
echo "van elders" > "$SANDBOX/other-gamma/elders.md"
git -C "$SANDBOX/other-gamma" add -A
git -C "$SANDBOX/other-gamma" commit -q -m "extern werk"
git -C "$SANDBOX/other-gamma" push -q origin main
echo "lokaal werk" > "$WS/gamma/lokaal.md"
rc=$(run gamma)
check "4a exitcode 0"           "$rc" "0"
check "4b alles gepusht"        "$(git -C "$WS/gamma" log origin/main..HEAD --oneline | wc -l | tr -d ' ')" "0"
check "4c extern werk behouden" "$(ls "$WS/gamma/elders.md" >/dev/null 2>&1 && echo ja)" "ja"
check "4d lokaal werk behouden" "$(ls "$WS/gamma/lokaal.md" >/dev/null 2>&1 && echo ja)" "ja"
grep -q "na rebase" "$LOG" && ok "4e rebase-pad gebruikt" || bad "4e rebase-pad niet gelogd"

# --- 5. achterlopend zonder lokaal werk: fast-forward pull ---
mkrepo delta
git clone -q "$SANDBOX/origin-delta.git" "$SANDBOX/other-delta"
git -C "$SANDBOX/other-delta" config user.email other@example.com
git -C "$SANDBOX/other-delta" config user.name Other
echo "nieuw" > "$SANDBOX/other-delta/nieuw.md"
git -C "$SANDBOX/other-delta" add -A && git -C "$SANDBOX/other-delta" commit -q -m "extern"
git -C "$SANDBOX/other-delta" push -q origin main
rc=$(run delta)
check "5a exitcode 0"        "$rc" "0"
check "5b pull uitgevoerd"   "$(ls "$WS/delta/nieuw.md" >/dev/null 2>&1 && echo ja)" "ja"

# --- 6. dry-run wijzigt niets ---
mkrepo epsilon
echo "niet committen" > "$WS/epsilon/los.md"
rc=$(run epsilon 1)
check "6a exitcode 0"            "$rc" "0"
check "6b nog ongecommit"        "$(git -C "$WS/epsilon" status --porcelain | wc -l | tr -d ' ')" "1"
check "6c geen nieuwe commit"    "$(git -C "$WS/epsilon" log --oneline | wc -l | tr -d ' ')" "1"
grep -q "DRY-RUN" "$LOG" && ok "6d dry-run gemeld" || bad "6d melding ontbreekt"

# --- 7. ontbrekende repo wordt netjes overgeslagen ---
rc=$(run "bestaat-niet")
check "7a exitcode 0" "$rc" "0"
grep -q "Sla over: geen git-directory" "$LOG" && ok "7b overslaan gelogd" || bad "7b melding ontbreekt"

# --- 8. stap 0: wat de inbox oplevert, wordt in DEZELFDE run gecommit ---
# Dit is de hele keten in één test: blob -> raw/ -> commit -> push. Zonder die
# laatste schakel zou het bestand pas de volgende nacht in git belanden.
mkrepo theta
mkinbox theta 0 "nieuw-boek.pdf"
rc=$(run_inbox theta theta)
check "8a exitcode 0"                "$rc" "0"
check "8b bestand in raw/"           "$(ls "$WS/theta/raw/nieuw-boek.pdf" >/dev/null 2>&1 && echo ja)" "ja"
check "8c werkmap schoon na run"     "$(git -C "$WS/theta" status --porcelain | wc -l | tr -d ' ')" "0"
check "8d raw-bestand gecommit"      "$(git -C "$WS/theta" ls-files raw/nieuw-boek.pdf | wc -l | tr -d ' ')" "1"
check "8e alles gepusht"             "$(git -C "$WS/theta" log origin/main..HEAD --oneline | wc -l | tr -d ' ')" "0"
grep -q "\[theta\] inbox: Token uit: teststub" "$LOG" && ok "8f uitvoer van het script in het log" || bad "8f uitvoer niet gelogd"
grep -q "Stap 0: upload-inbox van 1 vault" "$LOG" && ok "8g stap 0 aangekondigd" || bad "8g aankondiging ontbreekt"

# --- 9. mislukte inbox-sync: luid gemeld, maar de git-sync gaat door ---
mkrepo iota
mkinbox iota 1 ""
echo "gewoon werk" > "$WS/iota/wiki.md"
rc=$(run_inbox iota iota)
check "9a exitcode 1 (inbox-fout telt mee)" "$rc" "1"
check "9b git-sync tóch gedaan"             "$(git -C "$WS/iota" status --porcelain | wc -l | tr -d ' ')" "0"
check "9c werk gepusht"                     "$(git -C "$WS/iota" log origin/main..HEAD --oneline | wc -l | tr -d ' ')" "0"
grep -q "inbox-sync MISLUKT (exitcode 1)" "$LOG" && ok "9d mislukking luid gemeld" || bad "9d melding ontbreekt"
grep -q "1 inbox-sync(s) mislukt" "$LOG" && ok "9e in de eindregel geteld" || bad "9e niet in de eindregel"

# --- 10. vault zonder het script wordt netjes overgeslagen ---
mkrepo kappa
rc=$(run_inbox kappa kappa)
check "10a exitcode 0" "$rc" "0"
grep -q "\[kappa\] inbox: sla over" "$LOG" && ok "10b overslaan gelogd" || bad "10b melding ontbreekt"

# --- 11. dry-run haalt niets op ---
mkrepo lambda
mkinbox lambda 0 "zou-niet-moeten-bestaan.pdf"
rc=$(run_inbox lambda lambda 1)
check "11a exitcode 0"            "$rc" "0"
check "11b niets gedownload"      "$(ls "$WS/lambda/raw/zou-niet-moeten-bestaan.pdf" >/dev/null 2>&1 && echo ja || echo nee)" "nee"
grep -q "\[lambda\] inbox: --dry-run" "$LOG" && ok "11c --dry-run doorgegeven" || bad "11c vlag niet doorgegeven"

# --- 12. VAULT_SYNC_SKIP_INBOX=1 slaat stap 0 helemaal over ---
mkrepo mu
mkinbox mu 1 ""
VAULT_WORKSPACE="$WS" VAULT_SYNC_REPOS="mu" VAULT_SYNC_LOG="$LOG" \
  VAULT_SYNC_INBOX_VAULTS="mu" VAULT_SYNC_SKIP_INBOX=1 VAULT_SYNC_PYTHON=bash \
  bash "$SCRIPT" >/dev/null 2>&1
check "12a exitcode 0 (falende stub niet aangeroepen)" "$?" "0"
grep -q "Stap 0 (upload-inbox) overgeslagen" "$LOG" && ok "12b overslaan gelogd" || bad "12b melding ontbreekt"

echo
if [ "$fails" -gt 0 ]; then
  echo "=== $fails TEST(S) GEFAALD — log: $LOG ==="
  exit 1
fi
echo "=== alle sync-tests geslaagd ==="
rm -rf "$SANDBOX"
