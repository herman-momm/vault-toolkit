# launchd — de geplande taken van de vaults

De drie vault-taken draaien lokaal via macOS `launchd`. Er is geen cron, geen GitHub
Actions en geen scheduled cloud-agent. Deze map bevat de bestanden die daarvoor op het
systeem moeten staan; tot nu toe bestonden ze alleen daar en in geen enkele repo, en
daarmee was de automatisering het enige deel van deze opzet dat een schijfcrash niet zou
overleven.

## Wat er draait

| Tijd | Label | Wat het doet |
|---|---|---|
| 03:00 | `nl.dagzoom.daily-github-sync` | Commit ongecommit werk in alle vijf de repo's en synchroniseert met GitHub (push, fast-forward pull, of rebase+push). Weigert een repo als er een secret in de commit zou belanden. |
| 06:00 | `nl.dagzoom.daily-book-orchestrator` | Eén boek per dag uit één vault (round-robin) → wiki-pagina's, PDF, WordPress-**concept**, Capacities. |
| 07:15 | `nl.dagzoom.histvault-website-check` | Feedcontrole vanuit HistVault; kandidaten komen onderaan in `log.md` en in `status.md` van HistVault en GroeneVault. |

Staat de Mac op het geplande tijdstip uit, dan haalt `launchd` de slag in zodra hij wakker
is. De tijd die `wiki/status.md` noemt is dus de echte starttijd, en de volgorde van de
taken kan daardoor afwijken van de tabel hierboven.

## De drie lagen

```
~/Library/LaunchAgents/nl.dagzoom.<taak>.plist     ← schema (deze map)
        ↓
~/.config/histvault/<taak>.sh                      ← wrapper van drie regels (deze map)
        ↓
vault-toolkit/scripts/<taak>.sh                    ← de logica (git, scripts/)
   of HistVault/scripts/daily-website-check.sh
```

De wrapper in het midden lijkt overbodig maar is het niet: deze repo staat in iCloud
Drive, onder `Mobile Documents`, en `launchd` gaat slecht om met spaties in een pad in
`ProgramArguments`. De wrapper staat op een pad zonder spaties en geeft door met een
gequoteerd `exec`. Daardoor hoeft het plist nooit te veranderen als de logica verandert.

De logica zelf hoort **niet** in deze map — die staat in `scripts/` en wordt door de
testsuite gedekt. Hier staat alleen het schema en de doorgeefluik-laag.

## Terugzetten of wijzigen

```bash
launchd/install.sh            # vergelijkt systeem met git (ook zonder argument)
launchd/install.sh --dry-run  # toont wat --install zou doen
launchd/install.sh --install  # zet alles neer en herlaadt het in launchd
launchd/install.sh --adopt    # neemt een handmatige systeemwijziging over in git
```

`--check` is de normale gang van zaken: het zegt of wat er draait nog hetzelfde is als wat
hier staat, en toont per bestand de diff als dat niet zo is. Pas een schema dus bij
voorkeur hier aan en draai daarna `--install`; wijzig je toch eerst het systeem, haal het
dan met `--adopt` terug naar git voordat het stilletjes uiteenloopt.

Na een `--install` is de volgende controle `python3 scripts/vault-status.py` in een vault:
sectie *Automatisering* laat zien wanneer elke taak voor het laatst gedraaid heeft, en
wordt rood na 36 uur.

## Twee valkuilen die het al eens misgingen

**Logpaden nooit in iCloud.** `StandardOutPath` en `StandardErrorPath` moeten op een lokaal
pad staan (hier: `~/.config/histvault/`). Wijzen ze naar iCloud Drive, dan kan `launchd`
het proces niet starten en faalt de taak stil met `EX_CONFIG 78` — geen regel in het log,
geen melding, de taak draait simpelweg nooit. Dit heeft de websitecontrole anderhalve dag
plat gelegd voordat de sectie *Automatisering* in `status.md` het zichtbaar maakte.

**Absolute paden met de gebruikersnaam erin.** `launchd` vouwt `~` niet uit en kent geen
variabelen in `ProgramArguments`, dus de plists noemen `/Users/herman` letterlijk. Op een
andere Mac of onder een andere gebruikersnaam moeten die paden eerst worden aangepast;
`install.sh --install` weigert dan en zegt het.

## Niet in git

De `.bak-<datum>`-bestanden naast de wrappers en plists op het systeem — de
niet-versiebeheerde originelen van vóór deze opzet. Die zijn bewust blijven staan als
vangnet, maar horen hier niet thuis: git is nu het vangnet.
