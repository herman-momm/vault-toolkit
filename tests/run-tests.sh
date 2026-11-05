#!/bin/bash
# Draait alle testsuites van vault-toolkit en vat de uitkomst samen.
#
# Gebruik:
#   tests/run-tests.sh          # alles
#   tests/run-tests.sh -v       # met de volledige uitvoer per suite
#
# Exitcode 0 als alles slaagt, 1 zodra één suite faalt. Draai dit na elke
# wijziging in scripts/: de logica hier beheert drie vaults en committeert
# ongevraagd, dus een stille regressie is duur.
#
# De paden worden overal gequoteerd: dit project staat in iCloud Drive, onder
# "Mobile Documents", en een ongequoteerd pad valt op die spatie uiteen.
set -u

HIER="$(cd "$(dirname "$0")" && pwd)"
VERBOSE=0
[ "${1:-}" = "-v" ] && VERBOSE=1

falend=()
totaal=0
aantal=0

draai() { # $1 = omschrijving, $2.. = commando
  local naam="$1"; shift
  local uit rc n
  aantal=$((aantal + 1))
  printf '%-38s ' "$naam"
  uit=$("$@" 2>&1); rc=$?
  n=$(printf '%s\n' "$uit" | grep -c '^ok  ')
  totaal=$((totaal + n))
  if [ "$rc" -eq 0 ]; then
    echo "$n checks, geslaagd"
  else
    echo "GEFAALD (exitcode $rc)"
    falend+=("$naam")
  fi
  if [ "$VERBOSE" -eq 1 ]; then
    printf '%s\n\n' "$uit"
  fi
}

draai "link-hygiëne (gemini-book-runner)" python3 "$HIER/test_link_hygiene.py"
draai "github-sync (daily-github-sync)"   bash    "$HIER/test_github_sync.sh"
draai "store-ID (sync-blob-inbox)"        bash    "$HIER/test_store_id.sh"
draai "automatisering (vault-status)"      bash    "$HIER/test_automation_check.sh"
draai "launchd (plists + wrappers)"        bash    "$HIER/test_launchd.sh"

echo
if [ "${#falend[@]}" -gt 0 ]; then
  echo "=== GEFAALD: ${falend[*]} — draai met -v voor de details ==="
  exit 1
fi
echo "=== alles geslaagd: $totaal checks over $aantal suites ==="
