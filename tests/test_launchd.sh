#!/bin/bash
# Controleert de launchd-bestanden in launchd/: de drie plists, de drie wrappers
# en install.sh. Deze suite kijkt naar wat er in git staat, niet naar wat er op
# het systeem geladen is — drift tussen die twee is het werk van
# `launchd/install.sh --check`, niet van een test.
#
# Twee invarianten zijn hier de kern, omdat ze beide al eens misgingen:
#   1. geen logpad in iCloud Drive  -> launchd faalt stil met EX_CONFIG 78
#   2. de wrapper wijst naar een bestaand script in git
#
# Elke geslaagde controle print een regel die met "ok  " begint; run-tests.sh
# telt die. Exitcode 1 zodra er één faalt.
#
# Anders dan de andere suites werkt deze niet met synthetische fixtures maar met
# de echte bestanden in launchd/, want juist hun inhoud is wat gecontroleerd
# moet worden. Wel geldt één aanname over de omgeving: deze repo staat naast de
# vaults in dezelfde werkruimte, zodat het doelpad van een wrapper daarbinnen
# valt. Een losse kopie van alleen deze map faalt daarop — dat is geen
# regressie, maar het ontbreken van de vaults ernaast.
set -u

HIER="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HIER/.." && pwd)"
LD="$ROOT/launchd"
WERKRUIMTE="$(cd "$ROOT/.." && pwd)"

fouten=0

ok()    { echo "ok  $1"; }
faal()  { echo "FAAL: $1"; fouten=$((fouten + 1)); }

controleer() { # $1 = voorwaarde-uitkomst (0/1), $2 = omschrijving
  if [ "$1" -eq 0 ]; then ok "$2"; else faal "$2"; fi
}

# Verwacht schema per label: uur minuut wrapperbestand
beschrijf() {
  case "$1" in
    nl.dagzoom.daily-github-sync)       echo "3 0 daily-github-sync.sh" ;;
    nl.dagzoom.daily-book-orchestrator) echo "6 0 daily-book-orchestrator.sh" ;;
    nl.dagzoom.histvault-website-check) echo "7 15 daily-website-check-runner.sh" ;;
    *) echo "" ;;
  esac
}

LABELS=(
  nl.dagzoom.daily-github-sync
  nl.dagzoom.daily-book-orchestrator
  nl.dagzoom.histvault-website-check
)

lees() { # $1 = plist, $2 = sleutelpad
  /usr/libexec/PlistBuddy -c "Print $2" "$1" 2>/dev/null
}

echo "--- plists ---"
for l in "${LABELS[@]}"; do
  p="$LD/$l.plist"
  if [ ! -f "$p" ]; then
    faal "$l.plist bestaat"
    continue
  fi
  ok "$l.plist bestaat"

  plutil -lint "$p" >/dev/null 2>&1
  controleer $? "$l.plist is geldige XML-plist"

  label="$(lees "$p" ':Label')"
  [ "$label" = "$l" ]
  controleer $? "$l: Label komt overeen met de bestandsnaam"

  set -- $(beschrijf "$l")
  uur="$1"; minuut="$2"; wrapper="$3"

  [ "$(lees "$p" ':StartCalendarInterval:Hour')" = "$uur" ] \
    && [ "$(lees "$p" ':StartCalendarInterval:Minute')" = "$minuut" ]
  controleer $? "$l: draait om $(printf '%02d:%02d' "$uur" "$minuut")"

  # RunAtLoad uit: anders draait de taak bij elke herstart/herlaad mee.
  [ "$(lees "$p" ':RunAtLoad')" = "false" ]
  controleer $? "$l: RunAtLoad staat uit"

  # ProgramArguments: /bin/bash + het wrapperpad.
  [ "$(lees "$p" ':ProgramArguments:0')" = "/bin/bash" ]
  controleer $? "$l: wordt via /bin/bash gestart"

  doel="$(lees "$p" ':ProgramArguments:1')"
  [ "$(basename "$doel")" = "$wrapper" ]
  controleer $? "$l: roept wrapper $wrapper aan"

  # Het plist mag nooit rechtstreeks naar het iCloud-pad wijzen: spaties in
  # ProgramArguments zijn precies waarom de wrapper-laag bestaat.
  case "$doel" in
    *"Mobile Documents"*) faal "$l: ProgramArguments wijst in iCloud ($doel)" ;;
    *) ok "$l: ProgramArguments staat buiten iCloud" ;;
  esac

  # De regressie die de websitecontrole anderhalve dag plat legde.
  for sleutel in StandardOutPath StandardErrorPath; do
    pad="$(lees "$p" ":$sleutel")"
    if [ -z "$pad" ]; then
      faal "$l: $sleutel is gezet"
      continue
    fi
    case "$pad" in
      *"Mobile Documents"*|*"CloudDocs"*)
        faal "$l: $sleutel staat in iCloud ($pad) — launchd faalt dan stil met EX_CONFIG 78" ;;
      /*) ok "$l: $sleutel is een lokaal absoluut pad" ;;
      *)  faal "$l: $sleutel is geen absoluut pad ($pad)" ;;
    esac
  done
done

echo
echo "--- wrappers ---"
for l in "${LABELS[@]}"; do
  set -- $(beschrijf "$l")
  w="$LD/wrappers/$3"

  if [ ! -f "$w" ]; then
    faal "wrapper $3 bestaat"
    continue
  fi
  ok "wrapper $3 bestaat"

  [ -x "$w" ]
  controleer $? "wrapper $3 is uitvoerbaar"

  bash -n "$w" >/dev/null 2>&1
  controleer $? "wrapper $3 is syntactisch geldig"

  [ "$(head -1 "$w")" = "#!/bin/bash" ]
  controleer $? "wrapper $3 heeft een bash-shebang"

  # Precies één exec-regel, met het doelpad gequoteerd (de spatie in
  # "Mobile Documents" is anders fataal).
  n="$(grep -c '^exec ' "$w")"
  [ "$n" -eq 1 ]
  controleer $? "wrapper $3 heeft precies één exec-regel"

  grep -q '^exec /bin/bash "' "$w"
  controleer $? "wrapper $3 quote het doelpad"

  doel="$(grep '^exec ' "$w" | sed -n 's/.*exec \/bin\/bash "\([^"]*\)".*/\1/p')"
  if [ -z "$doel" ]; then
    faal "wrapper $3: doelpad is leesbaar"
    continue
  fi
  ok "wrapper $3: doelpad is leesbaar"

  # Het doel moet een script in git zijn: in deze toolkit of in een vault
  # ernaast, nooit in ~/.config (dan zou de logica weer buiten git liggen).
  case "$doel" in
    "$HOME/.config/"*) faal "wrapper $3 wijst naar ~/.config in plaats van naar git" ;;
    "$WERKRUIMTE/"*)   ok "wrapper $3 wijst naar een repo in de werkruimte" ;;
    *)                 faal "wrapper $3 wijst buiten de werkruimte ($doel)" ;;
  esac

  [ -f "$doel" ]
  controleer $? "wrapper $3: doelscript bestaat ($(basename "$doel"))"
done

echo
echo "--- install.sh en README ---"
[ -f "$LD/install.sh" ]; controleer $? "install.sh bestaat"
[ -x "$LD/install.sh" ]; controleer $? "install.sh is uitvoerbaar"
bash -n "$LD/install.sh" >/dev/null 2>&1; controleer $? "install.sh is syntactisch geldig"
[ -f "$LD/README.md" ]; controleer $? "README.md bestaat"

bash "$LD/install.sh" --help >/dev/null 2>&1
controleer $? "install.sh --help werkt"

bash "$LD/install.sh" --onzin >/dev/null 2>&1
[ $? -ne 0 ]
controleer $? "install.sh weigert een onbekende optie"

# install.sh moet alle drie de labels en alle drie de wrappers kennen; een
# vierde taak die hier wordt toegevoegd en daar vergeten, valt zo op.
for l in "${LABELS[@]}"; do
  grep -q "$l" "$LD/install.sh"
  controleer $? "install.sh kent $l"
done
for l in "${LABELS[@]}"; do
  set -- $(beschrijf "$l")
  grep -q "$3" "$LD/install.sh"
  controleer $? "install.sh kent wrapper $3"
done

# Elk plist en elke wrapper in de map moet ook in install.sh staan — anders
# wordt een nieuwe taak wel bewaard maar nooit geplaatst.
for p in "$LD"/*.plist; do
  naam="$(basename "$p" .plist)"
  grep -q "$naam" "$LD/install.sh"
  controleer $? "install.sh dekt het aanwezige $naam.plist"
done
for w in "$LD"/wrappers/*.sh; do
  grep -q "$(basename "$w")" "$LD/install.sh"
  controleer $? "install.sh dekt de aanwezige wrapper $(basename "$w")"
done

echo
if [ "$fouten" -gt 0 ]; then
  echo "=== $fouten controle(s) gefaald ==="
  exit 1
fi
echo "=== alle launchd-controles geslaagd ==="
