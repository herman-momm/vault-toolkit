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

run() {
  local dry="${2:-0}"
  VAULT_WORKSPACE="$WS" VAULT_SYNC_REPOS="$1" VAULT_SYNC_LOG="$LOG" \
    VAULT_SYNC_DRY_RUN="$dry" bash "$SCRIPT" >/dev/null 2>&1
  echo $?
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
mkrepo zeta
cat > "$WS/zeta/experiment.py" <<'PY'
import urllib.request
token = 'cap-api-REDACTED'
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

echo
if [ "$fails" -gt 0 ]; then
  echo "=== $fails TEST(S) GEFAALD — log: $LOG ==="
  exit 1
fi
echo "=== alle sync-tests geslaagd ==="
rm -rf "$SANDBOX"
