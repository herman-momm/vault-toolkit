#!/bin/bash
# Beheert de launchd-automatisering van de vaults: de drie plists in
# ~/Library/LaunchAgents en de drie wrappers in ~/.config/histvault.
#
# De bestanden hiernaast zijn de bron van waarheid; dit script zet ze op hun
# plek, of vergelijkt wat er staat met wat er in git staat.
#
#   launchd/install.sh              # of --check: alleen vergelijken (standaard)
#   launchd/install.sh --install    # git -> systeem, daarna herladen in launchd
#   launchd/install.sh --dry-run    # toont wat --install zou doen
#   launchd/install.sh --adopt      # systeem -> git (na een handmatige wijziging)
#
# Exitcode 0 als git en systeem gelijk zijn, 1 bij drift of een fout. Een
# --install of --adopt die slaagt geeft ook 0.
#
# Let op: de plists bevatten absolute paden met /Users/herman erin — launchd
# accepteert geen variabelen en ~ wordt niet uitgevouwen. Op een andere Mac of
# onder een andere gebruikersnaam moeten de paden dus eerst worden aangepast;
# het script weigert dan en zegt dat.
#
# Alle paden worden gequoteerd: deze repo staat in iCloud Drive, onder
# "Mobile Documents", en een ongequoteerd pad valt op die spatie uiteen.
set -u

HIER="$(cd "$(dirname "$0")" && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
WRAPPERS="$HOME/.config/histvault"
VERWACHT_HOME="/Users/herman"

LABELS=(
  nl.dagzoom.daily-github-sync
  nl.dagzoom.daily-book-orchestrator
  nl.dagzoom.histvault-website-check
)
WRAPPERNAMEN=(
  daily-github-sync.sh
  daily-book-orchestrator.sh
  daily-website-check-runner.sh
)

MODUS="check"
case "${1:-}" in
  ""|--check)  MODUS="check" ;;
  --install)   MODUS="install" ;;
  --dry-run)   MODUS="dry-run" ;;
  --adopt)     MODUS="adopt" ;;
  -h|--help)   sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "Onbekende optie: $1 — gebruik --check, --install, --dry-run of --adopt" >&2; exit 1 ;;
esac

fouten=0
drift=0

# Een paar van de plists noemen /Users/herman letterlijk. Installeren onder een
# andere HOME zou een agent opleveren die naar een onbestaand pad wijst.
if [ "$MODUS" = "install" ] && [ "$HOME" != "$VERWACHT_HOME" ]; then
  echo "WEIGERT: de plists hiernaast bevatten het letterlijke pad $VERWACHT_HOME," >&2
  echo "maar HOME is $HOME. Pas de paden in de plists eerst aan." >&2
  exit 1
fi

vergelijk() { # $1 = bestand in git, $2 = bestand op het systeem, $3 = omschrijving
  if [ ! -f "$2" ]; then
    echo "ONTBREEKT op het systeem: $3"
    drift=$((drift + 1))
    return
  fi
  if diff -q "$1" "$2" >/dev/null 2>&1; then
    echo "ok  $3"
  else
    echo "DRIFT: $3 — systeem wijkt af van git"
    diff -u "$1" "$2" | sed 's/^/      /'
    drift=$((drift + 1))
  fi
}

zet_neer() { # $1 = bron, $2 = doel, $3 = omschrijving, $4 = uitvoerbaar (ja/nee)
  if [ "$MODUS" = "dry-run" ]; then
    echo "zou kopiëren: $3 -> $2"
    return
  fi
  if ! cp "$1" "$2"; then
    echo "FOUT: kon $3 niet naar $2 kopiëren" >&2
    fouten=$((fouten + 1))
    return
  fi
  [ "$4" = "ja" ] && chmod +x "$2"
  echo "geplaatst: $3"
}

case "$MODUS" in
  check)
    for i in "${!LABELS[@]}"; do
      l="${LABELS[$i]}"
      vergelijk "$HIER/$l.plist" "$AGENTS/$l.plist" "plist $l"
    done
    for w in "${WRAPPERNAMEN[@]}"; do
      vergelijk "$HIER/wrappers/$w" "$WRAPPERS/$w" "wrapper $w"
    done
    echo
    if [ "$drift" -gt 0 ]; then
      echo "=== $drift verschil(len) tussen git en systeem ==="
      echo "Klopt git? Draai --install. Klopt het systeem? Draai --adopt."
      exit 1
    fi
    echo "=== systeem en git zijn gelijk ==="
    ;;

  install|dry-run)
    mkdir -p "$AGENTS" "$WRAPPERS"
    for i in "${!LABELS[@]}"; do
      l="${LABELS[$i]}"
      zet_neer "$HIER/$l.plist" "$AGENTS/$l.plist" "plist $l" nee
    done
    for w in "${WRAPPERNAMEN[@]}"; do
      zet_neer "$HIER/wrappers/$w" "$WRAPPERS/$w" "wrapper $w" ja
    done

    # Herladen, zodat een gewijzigd schema ook echt ingaat. bootout faalt als
    # de agent nog niet geladen was; dat is geen fout.
    for l in "${LABELS[@]}"; do
      if [ "$MODUS" = "dry-run" ]; then
        echo "zou herladen: $l"
        continue
      fi
      launchctl bootout "gui/$UID/$l" >/dev/null 2>&1
      if launchctl bootstrap "gui/$UID" "$AGENTS/$l.plist" 2>/dev/null; then
        echo "geladen: $l"
      else
        echo "FOUT: launchctl bootstrap weigerde $l" >&2
        fouten=$((fouten + 1))
      fi
    done
    echo
    if [ "$fouten" -gt 0 ]; then
      echo "=== $fouten fout(en) ==="
      exit 1
    fi
    [ "$MODUS" = "dry-run" ] && echo "=== dry-run: er is niets gewijzigd ===" \
                             || echo "=== alles geplaatst en geladen ==="
    ;;

  adopt)
    for i in "${!LABELS[@]}"; do
      l="${LABELS[$i]}"
      if [ -f "$AGENTS/$l.plist" ]; then
        zet_neer "$AGENTS/$l.plist" "$HIER/$l.plist" "plist $l" nee
      else
        echo "overgeslagen (niet op het systeem): plist $l"
      fi
    done
    for w in "${WRAPPERNAMEN[@]}"; do
      if [ -f "$WRAPPERS/$w" ]; then
        zet_neer "$WRAPPERS/$w" "$HIER/wrappers/$w" "wrapper $w" ja
      else
        echo "overgeslagen (niet op het systeem): wrapper $w"
      fi
    done
    echo
    if [ "$fouten" -gt 0 ]; then
      echo "=== $fouten fout(en) ==="
      exit 1
    fi
    echo "=== overgenomen in git — controleer met 'git diff' en commit ==="
    ;;
esac
