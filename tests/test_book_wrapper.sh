#!/bin/bash
# Controleert daily-book-summary.sh, de launchd-wrapper van de dagelijkse
# boektaak. Die wrapper is NIET geshimd: elke vault heeft een eigen kopie, die
# op één regel (VAULT=) van de andere verschilt. Dat is precies de constructie
# waar deze repo eerder drift op opliep, dus de gelijkheid van de drie kopieën
# is hier een harde invariant.
#
# Vier dingen gingen op 20 november 2026 samen mis en staan hier vast:
#   1. geen slaapassertie -> de Mac viel midden in de run terug in
#      'Maintenance Sleep' en de HTTP-verbinding van de CLI sneuvelde
#      ("Connection closed mid-response"); de run had toen ~61 seconden echt
#      gedraaid van de 16 minuten die het log suggereerde
#   2. de geünificeerde tak deed één poging, zonder retry
#   3. een afgebroken verbinding was in het log niet te onderscheiden van een
#      inhoudelijke fout
#   4. het script eindigde op een echo en kwam dus altijd als exit=0 terug,
#      waardoor de orchestrator "wrapper exit=0" meldde bij een mislukte run
#
# Net als test_launchd.sh werkt deze suite deels met de ECHTE bestanden in de
# drie vaults, want hun inhoud is wat gecontroleerd moet worden; het gedrag
# wordt daarnaast op een synthetische kopie in een wegwerpmap getest, met een
# nagemaakte claude-CLI. Er wordt dus nooit een echte CLI-aanroep gedaan.
#
# Elke geslaagde controle print een regel die met "ok  " begint; run-tests.sh
# telt die. Exitcode 1 zodra er één faalt.
set -u

HIER="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HIER/.." && pwd)"
WERKRUIMTE="$(cd "$ROOT/.." && pwd)"
NAAM="scripts/daily-book-summary.sh"
VAULTS=(ClaudeVault HistVault GroeneVault)

fouten=0
ok()   { echo "ok  $1"; }
faal() { echo "FAAL: $1"; fouten=$((fouten + 1)); }
controleer() { # $1 = uitkomst (0/1), $2 = omschrijving
  if [ "$1" -eq 0 ]; then ok "$2"; else faal "$2"; fi
}

bevat() { # $1 = bestand, $2 = patroon (ERE), $3 = omschrijving
  if grep -qE "$2" "$1"; then ok "$3"; else faal "$3"; fi
}

# --- 1. De drie kopieën bestaan, zijn uitvoerbaar en syntactisch geldig ------

aanwezig=()
for v in "${VAULTS[@]}"; do
  f="$WERKRUIMTE/$v/$NAAM"
  if [ ! -f "$f" ]; then
    faal "$v: $NAAM ontbreekt"
    continue
  fi
  aanwezig+=("$v")
  ok "$v: $NAAM aanwezig"
  [ -x "$f" ]; controleer $? "$v: wrapper is uitvoerbaar"
  bash -n "$f" 2>/dev/null; controleer $? "$v: wrapper is syntactisch geldig"

  # VAULT= moet naar de eigen vault wijzen, anders verwerkt een vault het
  # materiaal van een andere.
  eigen=$(grep -m1 '^VAULT=' "$f" | sed 's/^VAULT="//; s/"$//')
  [ "$eigen" = "$WERKRUIMTE/$v" ]
  controleer $? "$v: VAULT= wijst naar de eigen vault"
done

# --- 2. Drift: de kopieën mogen alleen op de VAULT-regel verschillen --------

if [ "${#aanwezig[@]}" -ge 2 ]; then
  ref="${aanwezig[0]}"
  for v in "${aanwezig[@]:1}"; do
    verschil=$(diff \
      <(grep -v '^VAULT=' "$WERKRUIMTE/$ref/$NAAM") \
      <(grep -v '^VAULT=' "$WERKRUIMTE/$v/$NAAM") | wc -l | tr -d ' ')
    [ "$verschil" -eq 0 ]
    controleer $? "$v: identiek aan $ref op de VAULT-regel na"
  done
else
  faal "minder dan twee wrappers gevonden — drift niet te controleren"
fi

# --- 3. De vier reparaties staan in elke kopie ------------------------------

for v in "${aanwezig[@]}"; do
  f="$WERKRUIMTE/$v/$NAAM"

  # (1) slaapassertie: caffeinate met alle drie de vlaggen, achter een
  # guard-variabele zodat het re-exec zich niet herhaalt.
  bevat "$f" 'exec caffeinate .*-i' "$v: caffeinate -i (idle sleep)"
  bevat "$f" 'exec caffeinate .*-s' "$v: caffeinate -s (system sleep)"
  bevat "$f" 'exec caffeinate .*-m' "$v: caffeinate -m (schijfslaap)"
  bevat "$f" 'if \[ -z "\$BOOKTASK_CAFFEINATED" \]' "$v: caffeinate achter een guard"
  # Het re-exec moet een absoluut pad meekrijgen en niet $0: launchd en de
  # orchestrator roepen absoluut aan, maar een handmatige relatieve aanroep na
  # een cd zou met $0 op zichzelf stuklopen.
  bevat "$f" 'exec caffeinate .*"\$VAULT/scripts/daily-book-summary\.sh"' \
    "$v: re-exec gebruikt het absolute vaultpad"

  # (2) retry op de geünificeerde tak, afgelezen aan de wachtrij.
  bevat "$f" 'QUEUE_TOP_BEFORE=' "$v: wachtrij-ijkpunt voor de retry"
  bevat "$f" 'while \[ "\$t" -lt "\$MAX_TRIES" \]' "$v: retry-lus aanwezig"

  # (3) een afgebroken verbinding wordt apart gelogd en stuurt NIET naar de
  # limiet-fallback: het juiste antwoord is een nieuwe poging.
  bevat "$f" 'connection closed' "$v: afgebroken verbinding wordt herkend"
  bevat "$f" 'Verbinding afgebroken' "$v: afgebroken verbinding wordt apart gelogd"

  # (4) exitcode doorgeven aan de orchestrator.
  bevat "$f" '^exit "\$FAILED"$' "$v: exitcode wordt doorgegeven"
  [ "$(grep -c '^exit "\$FAILED"$' "$f")" -eq 1 ]
  controleer $? "$v: precies één exit met de verzamelde status"
done

# --- 4. Gedrag: retry en exitcode op een synthetische kopie -----------------
# De echte wrapper wordt gekopieerd naar een wegwerpvault, met PAUSE=0 zodat de
# test niet een minuut per poging wacht, en met een nagemaakte claude-CLI.
# $HOME wijst naar de wegwerpmap, zodat claude.env met de echte tokens niet
# wordt gevonden: een test hoort geen credentials in een subproces te zetten.

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

bouw_fixture() { # $1 = naam van de nagemaakte CLI (stub-script), zet FVAULT
  FVAULT="$TMP/$1"
  mkdir -p "$FVAULT/scripts" "$FVAULT/raw" "$FVAULT/wiki"
  echo "boek.pdf" > "$FVAULT/scripts/book-queue.txt"
  echo "doe iets met het boek" > "$FVAULT/scripts/daily-book-prompt.md"
  echo "# vault" > "$FVAULT/CLAUDE.md"
  sed \
    -e "s|^VAULT=\".*\"$|VAULT=\"$FVAULT\"|" \
    -e "s|^CLAUDE=\".*\"$|CLAUDE=\"$TMP/$1-cli\"|" \
    -e 's|^PAUSE=.*|PAUSE=0|' \
    -e 's|^MAX_TRIES=.*|MAX_TRIES=2|' \
    "$WERKRUIMTE/${aanwezig[0]}/$NAAM" > "$FVAULT/scripts/daily-book-summary.sh"
  chmod +x "$FVAULT/scripts/daily-book-summary.sh"
}

# 4a. De CLI breekt elke keer af op een verbindingsfout en laat het boek staan.
bouw_fixture faal
cat > "$TMP/faal-cli" <<'STUB'
#!/bin/bash
echo "API Error: Connection closed mid-response. The response above may be incomplete."
exit 1
STUB
chmod +x "$TMP/faal-cli"

HOME="$TMP" /bin/bash "$FVAULT/scripts/daily-book-summary.sh" >/dev/null 2>&1
rc=$?
[ "$rc" -eq 1 ]
controleer $? "gedrag: mislukte run geeft exitcode 1 (was altijd 0)"

pogingen=$(grep -cE 'poging [0-9]+/2\) start' "$FVAULT/scripts/daily-book.log")
[ "$pogingen" -eq 2 ]
controleer $? "gedrag: de run doet $pogingen van 2 pogingen"

grep -q 'Verbinding afgebroken' "$FVAULT/scripts/daily-book.log"
controleer $? "gedrag: de afbreking staat als zodanig in het log"

grep -q 'boek.pdf' "$FVAULT/scripts/book-queue.txt"
controleer $? "gedrag: het boek blijft na een mislukking in de wachtrij"

# 4b. De CLI slaagt in één keer en popt de wachtrij: geen tweede poging.
bouw_fixture goed
cat > "$TMP/goed-cli" <<STUB
#!/bin/bash
: > "$FVAULT/scripts/book-queue.txt"
echo "klaar"
exit 0
STUB
chmod +x "$TMP/goed-cli"

HOME="$TMP" /bin/bash "$FVAULT/scripts/daily-book-summary.sh" >/dev/null 2>&1
rc=$?
[ "$rc" -eq 0 ]
controleer $? "gedrag: geslaagde run geeft exitcode 0"

pogingen=$(grep -cE 'poging [0-9]+/2\) start' "$FVAULT/scripts/daily-book.log")
[ "$pogingen" -eq 1 ]
controleer $? "gedrag: een geslaagde run doet geen tweede poging"

# 4c. Lege wachtrij: niets te doen, exitcode 0.
bouw_fixture leeg
: > "$FVAULT/scripts/book-queue.txt"
cp "$TMP/goed-cli" "$TMP/leeg-cli" 2>/dev/null || echo -e '#!/bin/bash\nexit 0' > "$TMP/leeg-cli"
chmod +x "$TMP/leeg-cli"
HOME="$TMP" /bin/bash "$FVAULT/scripts/daily-book-summary.sh" >/dev/null 2>&1
rc=$?
[ "$rc" -eq 0 ]
controleer $? "gedrag: lege wachtrij geeft exitcode 0"

grep -q 'queue leeg' "$FVAULT/scripts/daily-book.log"
controleer $? "gedrag: lege wachtrij wordt gelogd"

echo
if [ "$fouten" -gt 0 ]; then
  echo "=== $fouten controle(s) GEFAALD ==="
  exit 1
fi
echo "=== alle controles geslaagd ==="
