# vault-toolkit

Gedeelde automatiseringsscripts voor de LLM Wiki-vaults (ClaudeVault, HistVault,
GroeneVault, …). Deze scripts bevatten geen vaultspecifieke content — alleen
generieke logica voor boeksamenvattingen, PDF-export, publicatie en
statusrapportage.

## Gebruik

Draai elk script **vanuit de root van de vault die je verwerkt**:

```bash
cd /pad/naar/<vault>                 # ClaudeVault, HistVault, GroeneVault
python3 scripts/vault-status.py --sync-sources
python3 scripts/wp-publish-draft.py exports/<bestand>.md --type knowledgebase ...
```

Dat `scripts/<naam>.py` in de vault is een **shim**: een bestand van twintig
regels zonder logica dat de implementatie hier opzoekt en aanroept. Zo is er
één implementatie, terwijl elke vault (en elke CLAUDE.md) het korte, vertrouwde
pad blijft gebruiken. De shim zoekt de toolkit in deze volgorde:

1. `$VAULT_TOOLKIT`
2. een checkout naast de vault (`<werkruimte>/vault-toolkit`)
3. `/tmp/vault-toolkit` — waar een cloud-sessie hem kloont
4. het iCloud-pad in de LLM-Workspace

In een cloud-sessie werkt hetzelfde commando dus ook, zolang stap 1 van de
dagelijkse taak de toolkit kloont:

```bash
git clone --depth 1 https://github.com/herman-momm/vault-toolkit /tmp/vault-toolkit
```

**Vaultresolutie.** De scripts werken relatief aan de vault die ze verwerken,
bepaald via `--vault` (waar ondersteund), anders `$VAULT_ROOT` — die de shim
zelf zet op zijn eigen vault — anders de werkmap, anders de map boven het
script. Een verkeerde werkmap levert dus geen stille lege scan meer op, en de
shim werkt ook als je hem van buiten de vault aanroept.

## Scripts

| Script | Doel |
|---|---|
| `vaultenv.py` | Gedeelde vault- en credentialresolutie; door de andere scripts geïmporteerd |
| `vault-status.py` | Genereert `wiki/status.md` (ingest-achterstand, linkhygiëne, drift) |
| `vault-find.py` | Zoekt door de wiki-pagina's van een vault |
| `sync-blob-inbox.py` | Haalt uploads uit de Vercel Blob-inbox naar `raw/` |
| `html2md.py` | Zet een opgehaalde HTML-pagina om naar schone Markdown |
| `pdf-outline.py` | Print de outline/structuur van een PDF |
| `epub-cover.py` | Cover-afbeelding uit een EPUB halen → `exports/covers/` |
| `md2pdf.py` | Export-markdown naar huisstijl-PDF (A4, tabellen, callouts, inline afbeeldingen) |
| `gemini-book-runner.py` | Volautomatische boeksamenvatting via Gemini (fallback als de Claude CLI aan zijn limiet zit) |
| `gemini-fallback.py` | Kale Gemini API-caller voor losse vault-taken |
| `capacities-push.py` | Capacities API: Boek-objecten, pagina's, daily note |
| `fill-capacities-photos.py` | Zoekt en koppelt foto's bij Capacities-persoonobjecten |
| `wp-publish-draft.py` | Publiceert een export als WordPress-**concept** (Knowledge Base e.d.) |
| `wp-book-showcase.py` | Publiceert een boek naar de Book Showcase-plugin |
| `wp-upload-cover.py` | Upload een cover naar de WP-mediabibliotheek, print de publieke URL |
| `daily-book-orchestrator.sh` | Kiest één vault per dag (round-robin) en start diens boekwrapper — launchd 06:00 |
| `daily-github-sync.sh` | Commit ongecommit werk en synchroniseert alle repo's met GitHub — launchd 03:00 |

De twee `.sh`-scripts worden door launchd aangeroepen via een wrapper van drie
regels in `~/.config/histvault/`, omdat launchd slecht omgaat met spaties in het
iCloud-pad. De logica staat hier, in git; de wrapper geeft alleen door.

## Credentials

`vaultenv.py` bepaalt waar een credentialbestand (`wp.env`, `capacities.env`,
`claude.env`) staat. Eerste bestand dat bestaat wint:

1. `$VAULT_<NAAM>_ENV` — bijv. `$VAULT_WP_ENV` voor `wp.env`
2. `~/.config/<vaultnaam>/<naam>.env` — per-vault override
3. `~/.config/vaults/<naam>.env` — expliciet gedeeld
4. `~/.config/histvault/<naam>.env` — historisch gedeelde locatie; hier staat
   vandaag alles, ook voor ClaudeVault en GroeneVault

Een gezette environment variable gaat altijd vóór het bestand, zodat een
cloud-sessie zonder credentialbestand gewoon werkt.

Voor de Vercel Blob-tokens geldt daarnaast een **vault-specifieke** naam, zodat
drie vaults in één omgeving elkaars store niet raken:
`BLOB_READ_WRITE_TOKEN_HIST` (HistVault), `..._GREEN` (GroeneVault), de
generieke naam (ClaudeVault). `sync-blob-inbox.py` valt als laatste terug op
`web/.env.local` van de vault zelf en meldt altijd welke bron het gebruikte.

## Versiebeheer tussen vaults

Eén implementatie hier, shims in de vaults — een wijziging in dit repo geldt
dus meteen voor alle vaults zonder nasynchroniseren. Dat is bewust zo gemaakt
nadat drie vaults op uiteengelopen kopieën bleken te draaien (twee met een
oudere WordPress-publicatie, één met een NFC-fix die de andere misten).

Een cloud-sessie kloont dit repo vers bij elke run naar `/tmp/vault-toolkit`;
lokaal gebruiken de shims de checkout naast de vaults, die de nachtelijke
GitHub-sync (03:00) bijwerkt.
