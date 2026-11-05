#!/bin/bash
# Wrapper voor launchd (nl.dagzoom.daily-github-sync, 03:00).
#
# De logica zelf staat in git, in vault-toolkit/scripts/daily-github-sync.sh —
# dit bestand geeft alleen door, zodat de automatisering versiebeheerd is en
# launchd toch dit vaste pad kan blijven aanroepen (spaties in het iCloud-pad
# zijn voor launchd lastig; daarom deze tussenstap).
# De originele, niet-versiebeheerde versie staat hiernaast als .bak-<datum>.
exec /bin/bash "/Users/herman/Library/Mobile Documents/com~apple~CloudDocs/LLM-Workspace/vault-toolkit/scripts/daily-github-sync.sh" "$@"
