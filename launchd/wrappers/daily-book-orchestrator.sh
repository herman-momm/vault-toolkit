#!/bin/bash
# Wrapper voor launchd (nl.dagzoom.daily-book-orchestrator, 06:00).
#
# De logica zelf staat in git, in vault-toolkit/scripts/daily-book-orchestrator.sh —
# dit bestand geeft alleen door, zodat de automatisering versiebeheerd is en
# launchd toch dit vaste pad kan blijven aanroepen.
# De originele, niet-versiebeheerde versie staat hiernaast als .bak-<datum>.
exec /bin/bash "/Users/herman/Library/Mobile Documents/com~apple~CloudDocs/LLM-Workspace/vault-toolkit/scripts/daily-book-orchestrator.sh" "$@"
