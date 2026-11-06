#!/bin/bash
# daily-github-sync.sh — nachtelijke git-sync van alle vault-repositories.
#
# Aangeroepen door launchd (nl.dagzoom.daily-github-sync, 03:00) via de wrapper
# ~/.config/histvault/daily-github-sync.sh. Draait de Mac om 03:00 niet, dan
# haalt launchd de slag in zodra hij wakker is — de tijd in het log is dus de
# echte starttijd.
#
# Stap 0, vóór de git-ronde: de /upload-INBOX van elke vault leeghalen
# (`sync-blob-inbox.py`). Zonder deze stap bleef een geüpload bestand in de
# Vercel-blob staan tot iemand een interactieve sessie opende — onzichtbaar voor
# elk dashboard, want `vault-status.py` kijkt naar `raw/` en niet naar de blob.
# Nu landt het bestand in `raw/`, wordt het in dezelfde run gecommit, en staat de
# ingest-achterstand de volgende ochtend rood op `wiki/status.md`. Het ophalen
# zelf is geen LLM-werk (een HTTP-call), dus dit kost geen sessiegebruik — de
# reden dat de uurlijkse taak op 3 nov 2026 verdween gold de agent, niet het
# transport. Een mislukte inbox-sync houdt de git-sync nooit tegen; die is de
# kritieke taak.
#
# Werking per repo:
#   1. COMMIT  — ongecommit werk wordt vastgelegd (git add -A + commit). Zonder
#      deze stap pushte dit script alleen wat iemand met de hand had gecommit,
#      waardoor sessiewerk buiten git bleef staan.
#   2. PUSH    — lokale commits naar origin.
#   3. PULL    — geen lokale commits maar wel achterstand? fast-forward pull.
#      Is de branch uiteengelopen (push geweigerd), dan rebase op origin en
#      opnieuw pushen; bij een conflict: rebase afbreken en melden.
#
# Veiligheidsrem, twee lagen: komt er een bestand in de commit waarvan de NAAM
# op een secret lijkt (.env, *token*, *secret*, *credential*, *.pem/key), óf
# waarvan de INHOUD een herkenbare sleutel bevat (cap-api-, vercel_blob_rw_,
# sk-, ghp_, AKIA…, xox…, een PRIVATE KEY-blok), dan wordt die repo
# OVERGESLAGEN en luid gemeld. De .gitignore's dekken de bekende bestanden al,
# maar een onbeheerde `git add -A` om 03:00 verdient een tweede slot — en de
# naamcheck alleen is niet genoeg: op 5 nov 2026 stonden er in ClaudeVault twee
# losse experimentscripts (inspect_personen.py, test_cap.py) met een Capacities
# API-token in platte tekst, die geen enkele naamregel zou hebben tegengehouden.
#
# Overrides (voor een test of een eenmalige run):
#   VAULT_WORKSPACE=<pad>            andere werkruimte
#   VAULT_SYNC_REPOS="a b c"         andere repolijst
#   VAULT_SYNC_LOG=<pad>             ander logbestand
#   VAULT_SYNC_DRY_RUN=1             niets committen/pushen, alleen loggen
#   VAULT_SYNC_INBOX_VAULTS="a b"    andere vaults voor de inbox-stap
#   VAULT_SYNC_SKIP_INBOX=1          stap 0 overslaan
#   VAULT_SYNC_PYTHON=<pad>          andere python voor de inbox-stap

WORKSPACE="${VAULT_WORKSPACE:-/Users/herman/Library/Mobile Documents/com~apple~CloudDocs/LLM-Workspace}"
LOG="${VAULT_SYNC_LOG:-$HOME/.config/histvault/daily-github-sync.log}"
LOG_MAX_LINES="${VAULT_SYNC_LOG_MAX_LINES:-4000}"
DRY_RUN="${VAULT_SYNC_DRY_RUN:-0}"

if [ -n "$VAULT_SYNC_REPOS" ]; then
  # shellcheck disable=SC2206
  REPOS=($VAULT_SYNC_REPOS)
else
  REPOS=(
    "ClaudeVault"
    "HistVault"
    "GroeneVault"
    "vault-toolkit"
    "dagzoom-vaults-hub"
  )
fi

# Vaults met een /upload-inbox. vault-toolkit en dagzoom-vaults-hub hebben geen
# raw/ en geen blob-store, dus die doen aan stap 0 niet mee.
if [ -n "$VAULT_SYNC_INBOX_VAULTS" ]; then
  # shellcheck disable=SC2206
  INBOX_VAULTS=($VAULT_SYNC_INBOX_VAULTS)
else
  INBOX_VAULTS=(
    "ClaudeVault"
    "HistVault"
    "GroeneVault"
  )
fi
SKIP_INBOX="${VAULT_SYNC_SKIP_INBOX:-0}"
PYTHON="${VAULT_SYNC_PYTHON:-python3}"

# gh credential helper en git in PATH (launchd start met een minimale omgeving)
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin:$PATH"

mkdir -p "$(dirname "$LOG")"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*" >> "$LOG"
}

# Houd het log hanteerbaar: bewaar de laatste LOG_MAX_LINES regels.
rotate_log() {
  [ -f "$LOG" ] || return 0
  local lines
  lines=$(wc -l < "$LOG" | tr -d ' ')
  if [ "$lines" -gt "$LOG_MAX_LINES" ]; then
    tail -n "$LOG_MAX_LINES" "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    log "log ingekort tot de laatste $LOG_MAX_LINES regels (was $lines)."
  fi
}

# Herkenbare sleutelvormen in bestandsinhoud. Bewust nauw gehouden: liever een
# gemiste exoot dan elke nacht een valse weigering op gewone vaultinhoud.
SECRET_CONTENT_RE='cap-api-[A-Za-z0-9_-]{16}|vercel_blob_rw_[A-Za-z0-9_-]{16}|sk-[A-Za-z0-9]{20}|sk-ant-[A-Za-z0-9_-]{16}|ghp_[A-Za-z0-9]{20}|github_pat_[A-Za-z0-9_]{20}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10}|-----BEGIN [A-Z ]*PRIVATE KEY-----'
SECRET_SCAN_MAX_BYTES="${VAULT_SYNC_SECRET_MAX_BYTES:-2000000}"

# Verdachte NAAM in een NUL-gescheiden padenstroom op stdin.
scan_names() {
  tr '\0' '\n' \
    | grep -iE '(^|/)\.env|(^|/)[^/]*(secret|credential|token)[^/]*\.(env|json|txt|yml|yaml)$|\.(pem|p12|key)$' \
    || true
}

# Sleutel IN de inhoud, voor een NUL-gescheiden padenstroom op stdin. Binaire
# bestanden (grep -I) en grote bestanden (PDF's, EPUB's) worden overgeslagen.
scan_contents() {
  local dir="$1" f size
  while IFS= read -r -d '' f; do
    [ -f "$dir/$f" ] || continue
    size=$(wc -c < "$dir/$f" 2>/dev/null | tr -d ' ')
    if [ -n "$size" ] && [ "$size" -gt "$SECRET_SCAN_MAX_BYTES" ]; then continue; fi
    if grep -I -q -E "$SECRET_CONTENT_RE" "$dir/$f" 2>/dev/null; then
      echo "$f"
    fi
  done
}

# Verdachte bestanden in de STAGED set (beide lagen, zonder dubbele regels).
staged_secrets() {
  local dir="$1"
  {
    git -C "$dir" diff --cached --name-only -z | scan_names
    git -C "$dir" diff --cached --name-only -z | scan_contents "$dir"
  } | sort -u | grep -v '^$' || true
}

# Verdachte bestanden in de WERKMAP (gewijzigd + ongetrackt, .gitignore
# gerespecteerd). Nodig voor de dry-run, die niets staged en anders zou melden
# dat hij bestanden zou committen die hij in werkelijkheid zou weigeren.
dirty_secrets() {
  local dir="$1"
  {
    git -C "$dir" ls-files -moz --exclude-standard | scan_names
    git -C "$dir" ls-files -moz --exclude-standard | scan_contents "$dir"
  } | sort -u | grep -v '^$' || true
}

# Leg ongecommit werk vast. Retour: 0 = commit gemaakt, 1 = niets te doen, 2 = geweigerd.
commit_worktree() {
  local dir="$1" repo="$2"
  local dirty
  dirty=$(git -C "$dir" status --porcelain | wc -l | tr -d ' ')
  [ "$dirty" -eq 0 ] && return 1

  if [ "$DRY_RUN" = "1" ]; then
    local would
    would=$(dirty_secrets "$dir")
    if [ -n "$would" ]; then
      log "[$repo] DRY-RUN: zou deze repo WEIGEREN (dus ook niet pushen) — verdacht bestand(en):"
      while IFS= read -r s; do [ -n "$s" ] && log "[$repo]    $s"; done <<< "$would"
      return 2
    fi
    log "[$repo] DRY-RUN: zou $dirty bestand(en) committen."
    return 1
  fi

  git -C "$dir" add -A

  local secrets
  secrets=$(staged_secrets "$dir")
  if [ -n "$secrets" ]; then
    log "[$repo] !! GEWEIGERD: de commit zou een secret bevatten — repo overgeslagen:"
    while IFS= read -r s; do [ -n "$s" ] && log "[$repo]    $s"; done <<< "$secrets"
    git -C "$dir" reset >> "$LOG" 2>&1
    return 2
  fi

  local staged
  staged=$(git -C "$dir" diff --cached --name-only | wc -l | tr -d ' ')
  [ "$staged" -eq 0 ] && return 1

  # Korte samenvatting per toplaag-map, bv. "wiki/: 8 · exports/: 3"
  local summary
  summary=$(git -C "$dir" diff --cached --name-only \
    | awk -F/ '{print (NF>1 ? $1"/" : "(root)")}' \
    | sort | uniq -c | sort -rn \
    | awk '{printf "%s%s: %s", (NR>1 ? " · " : ""), $2, $1}')

  local msg="auto: dagelijkse sync $(date '+%Y-%m-%d') ($staged bestand(en))"
  if git -C "$dir" commit -q -m "$msg" -m \
      "Automatisch vastgelegd door daily-github-sync.sh, zodat werk dat in een sessie niet is gecommit alsnog in git en op GitHub belandt." \
      -m "$summary" >> "$LOG" 2>&1; then
    log "[$repo] $staged bestand(en) gecommit — $summary"
    return 0
  fi
  log "[$repo] FOUT: commit mislukt."
  git -C "$dir" reset >> "$LOG" 2>&1
  return 2
}

# Stap 0: haal de /upload-inbox van elke vault leeg naar raw/.
#
# Dit draait vóór de git-ronde, zodat wat hier binnenkomt in dezelfde run door
# commit_worktree() wordt vastgelegd. De uitvoer van het script gaat regel voor
# regel het log in: dat is de enige plek waar later te zien is wélk bestand is
# opgehaald, en of het token uit de verwachte bron kwam.
#
# Een mislukking (geen token, store-ID-mismatch, API-fout) wordt luid gemeld en
# telt mee in de exitcode, maar breekt niets af: de git-sync is de kritieke taak
# en moet ook doorgaan als de blob onbereikbaar is.
sync_inboxes() {
  local vault dir script uit rc regel
  for vault in "${INBOX_VAULTS[@]}"; do
    dir="$WORKSPACE/$vault"
    script="$dir/scripts/sync-blob-inbox.py"
    if [ ! -f "$script" ]; then
      log "[$vault] inbox: sla over — $script niet gevonden."
      continue
    fi

    # VAULT_ROOT expliciet: het script leidt zijn doelmap af uit de werkmap of
    # uit die variabele, en we willen niet afhangen van wat de shim zet.
    if [ "$DRY_RUN" = "1" ]; then
      uit=$(cd "$dir" && VAULT_ROOT="$dir" "$PYTHON" "$script" --dry-run 2>&1)
      rc=$?
    else
      uit=$(cd "$dir" && VAULT_ROOT="$dir" "$PYTHON" "$script" 2>&1)
      rc=$?
    fi

    while IFS= read -r regel; do
      [ -n "$regel" ] && log "[$vault] inbox: $regel"
    done <<< "$uit"

    if [ "$rc" -ne 0 ]; then
      log "[$vault] !! inbox-sync MISLUKT (exitcode $rc) — er is niets opgehaald."
      inbox_fail=$((inbox_fail + 1))
    fi
  done
}

{
  echo ""
  echo "===================================================================="
} >> "$LOG"
log "START dagelijkse GitHub-sync (launchd 03:00)"
[ "$DRY_RUN" = "1" ] && log "DRY-RUN actief: er wordt niets gecommit of gepusht."

success_count=0
fail_count=0
commit_count=0
inbox_fail=0

if [ "$SKIP_INBOX" = "1" ]; then
  log "Stap 0 (upload-inbox) overgeslagen: VAULT_SYNC_SKIP_INBOX=1."
else
  log "Stap 0: upload-inbox van ${#INBOX_VAULTS[@]} vault(s) ophalen naar raw/..."
  sync_inboxes
fi

for repo in "${REPOS[@]}"; do
  dir="$WORKSPACE/$repo"
  if [ ! -d "$dir/.git" ]; then
    log "[$repo] Sla over: geen git-directory gevonden in $dir"
    continue
  fi

  log "[$repo] Synchroniseren..."
  branch=$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")

  commit_worktree "$dir" "$repo"
  case $? in
    0) commit_count=$((commit_count + 1)) ;;
    2) fail_count=$((fail_count + 1)); continue ;;
  esac

  git -C "$dir" fetch origin "$branch" >> "$LOG" 2>&1

  unpushed=$(git -C "$dir" log "origin/$branch..HEAD" --oneline 2>/dev/null | wc -l | tr -d ' ')

  if [ "$unpushed" -gt 0 ]; then
    if [ "$DRY_RUN" = "1" ]; then
      log "[$repo] DRY-RUN: zou $unpushed commit(s) pushen."
      success_count=$((success_count + 1))
      continue
    fi
    log "[$repo] $unpushed lokale commit(s) -> pushen naar origin/$branch..."
    if git -C "$dir" push origin "$branch" >> "$LOG" 2>&1; then
      log "[$repo] Push geslaagd."
      success_count=$((success_count + 1))
    else
      # Branch uiteengelopen: rebase op origin en één keer opnieuw proberen.
      log "[$repo] Push geweigerd — rebase op origin/$branch en opnieuw proberen..."
      if git -C "$dir" rebase "origin/$branch" >> "$LOG" 2>&1 \
         && git -C "$dir" push origin "$branch" >> "$LOG" 2>&1; then
        log "[$repo] Push geslaagd na rebase."
        success_count=$((success_count + 1))
      else
        git -C "$dir" rebase --abort >> "$LOG" 2>&1
        log "[$repo] FOUT: push mislukt, rebase afgebroken — handmatig oplossen."
        fail_count=$((fail_count + 1))
      fi
    fi
  else
    unpulled=$(git -C "$dir" log "HEAD..origin/$branch" --oneline 2>/dev/null | wc -l | tr -d ' ')
    if [ "$unpulled" -gt 0 ]; then
      if [ "$DRY_RUN" = "1" ]; then
        log "[$repo] DRY-RUN: zou $unpulled commit(s) pullen."
        success_count=$((success_count + 1))
        continue
      fi
      log "[$repo] $unpulled externe commit(s) op origin/$branch -> fast-forward pull..."
      if git -C "$dir" pull --ff-only origin "$branch" >> "$LOG" 2>&1; then
        log "[$repo] Pull geslaagd."
        success_count=$((success_count + 1))
      else
        log "[$repo] FOUT: pull mislukt."
        fail_count=$((fail_count + 1))
      fi
    else
      log "[$repo] Reeds up-to-date met origin/$branch."
      success_count=$((success_count + 1))
    fi
  fi
done

log "EINDE sync: $success_count geslaagd, $fail_count mislukt, $commit_count repo(s) met een nieuwe auto-commit, $inbox_fail inbox-sync(s) mislukt."
echo "====================================================================" >> "$LOG"
rotate_log

exit $((fail_count + inbox_fail))
