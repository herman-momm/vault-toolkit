#!/bin/bash
# daily-book-orchestrator.sh — verwerkt PRECIES ÉÉN boek per dag, uit ÉÉN vault.
#
# Aangeroepen door launchd (nl.dagzoom.daily-book-orchestrator, 06:00) via de
# wrapper ~/.config/histvault/daily-book-orchestrator.sh. Dit draait LOKAAL op
# de Mac, niet als cloud-agent; staat de Mac om 06:00 uit, dan haalt launchd de
# slag in zodra hij wakker is.
#
# Werking:
#   - Rouleert over de vaults hieronder; vaults met een lege wachtrij worden
#     overgeslagen.
#   - Maakt eerst onderbroken werk af: heeft een vault nog een openstaande
#     handoff (vorige run halverwege afgebroken), dan krijgt die voorrang.
#   - Roept de per-vault wrapper `daily-book-summary.sh` aan. Die probeert
#     eerst de Claude CLI met de prompts in scripts/daily-book-prompt*.md en
#     valt bij een rate limit terug op scripts/daily-book-fallback.py.
#   - Onthoudt in een state-bestand welke vault als laatste draaide.
#
# Overrides (voor een test of een eenmalige run):
#   VAULT_WORKSPACE=<pad>        andere werkruimte
#   VAULT_BOOK_VAULTS="a b c"    andere rotatievolgorde (mapnamen)
#   VAULT_BOOK_STATE=<pad>       ander state-bestand
#   VAULT_BOOK_LOG=<pad>         ander logbestand
#   VAULT_BOOK_DRY_RUN=1         alleen tonen welke vault zou draaien

WORKSPACE="${VAULT_WORKSPACE:-/Users/herman/Library/Mobile Documents/com~apple~CloudDocs/LLM-Workspace}"
STATE="${VAULT_BOOK_STATE:-$HOME/.config/histvault/daily-book-rotation.state}"
OLOG="${VAULT_BOOK_LOG:-$HOME/.config/histvault/daily-book-orchestrator.log}"
LOG_MAX_LINES="${VAULT_BOOK_LOG_MAX_LINES:-4000}"
DRY_RUN="${VAULT_BOOK_DRY_RUN:-0}"

# Rotatievolgorde: mapnamen binnen de werkruimte.
if [ -n "${VAULT_BOOK_VAULTS:-}" ]; then
  # shellcheck disable=SC2206
  NAMES=($VAULT_BOOK_VAULTS)
else
  NAMES=(HistVault GroeneVault ClaudeVault)
fi
n=${#NAMES[@]}

mkdir -p "$(dirname "$OLOG")" "$(dirname "$STATE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*" >> "$OLOG"; }

rotate_log() {
  [ -f "$OLOG" ] || return 0
  local lines
  lines=$(wc -l < "$OLOG" | tr -d ' ')
  if [ "$lines" -gt "$LOG_MAX_LINES" ]; then
    tail -n "$LOG_MAX_LINES" "$OLOG" > "$OLOG.tmp" && mv "$OLOG.tmp" "$OLOG"
    log "log ingekort tot de laatste $LOG_MAX_LINES regels (was $lines)."
  fi
}

has_work() {      # $1 = vault-root ; wachtrij niet leeg?
  local q="$1/scripts/book-queue.txt"
  [ -s "$q" ] && grep -q '[^[:space:]]' "$q"
}
has_handoff() {   # $1 = vault-root ; openstaande handoff (onderbroken run)?
  [ -f "$1/scripts/.daily-handoff.json" ] || [ -f "$1/scripts/.daily-handoff-A1.json" ]
}

last=$(cat "$STATE" 2>/dev/null)
[[ "$last" =~ ^[0-9]+$ ]] || last=-1

log "start — vorige index: $last"

pick=-1
# Pass 1: onderbroken werk eerst afmaken (in rotatievolgorde vanaf last+1).
for ((i=1;i<=n;i++)); do
  idx=$(( (last + i) % n ))
  root="$WORKSPACE/${NAMES[$idx]}"
  if has_handoff "$root"; then pick=$idx; log "voorrang: onderbroken run in ${NAMES[$idx]}"; break; fi
done
# Pass 2: anders de volgende vault met een niet-lege wachtrij.
if [ "$pick" -lt 0 ]; then
  for ((i=1;i<=n;i++)); do
    idx=$(( (last + i) % n ))
    root="$WORKSPACE/${NAMES[$idx]}"
    if has_work "$root"; then pick=$idx; break; fi
  done
fi

if [ "$pick" -lt 0 ]; then
  log "alle wachtrijen leeg en geen openstaande handoff — niets te doen."
  rotate_log
  exit 0
fi

name="${NAMES[$pick]}"
root="$WORKSPACE/$name"
wrapper="$root/scripts/daily-book-summary.sh"

if [ "$DRY_RUN" = "1" ]; then
  log "DRY-RUN: zou $name (index $pick) verwerken via $wrapper"
  rotate_log
  exit 0
fi

log "gekozen vault: $name  (index $pick) — start $wrapper"
if [ -f "$wrapper" ]; then
  /bin/bash "$wrapper"
  rc=$?
  log "$name klaar (wrapper exit=$rc)"
  # Alleen doorschuiven als de vault echt verwerkt is (geen resterende handoff).
  # Blijft er een handoff staan (afgebroken), dan NIET doorschuiven, zodat
  # dezelfde vault morgen eerst wordt afgemaakt.
  if has_handoff "$root"; then
    log "$name heeft nog een openstaande handoff — rotatie NIET doorgeschoven (morgen afmaken)."
  else
    echo "$pick" > "$STATE"
    log "rotatie doorgeschoven naar index $pick als 'laatste'."
  fi
else
  log "FOUT: wrapper niet gevonden voor $name ($wrapper)"
  rotate_log
  exit 1
fi

rotate_log
