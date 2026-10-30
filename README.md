# vault-toolkit

Gedeelde automatiseringsscripts voor de LLM Wiki-vaults (ClaudeVault, HistVault,
GroeneVault, …). Deze scripts bevatten geen vaultspecifieke content — alleen
generieke logica voor boeksamenvattingen, PDF-export en statusrapportage.

## Gebruik

Draai elk script **altijd vanuit de root van de vault die je verwerkt**, niet
vanuit deze toolkit-checkout. Typisch patroon in een dagelijkse taak:

```bash
cd /pad/naar/<vault>                      # bv. claudevault, histvault, groenevault
git clone --depth 1 https://github.com/herman-momm/vault-toolkit /tmp/vault-toolkit
python3 /tmp/vault-toolkit/scripts/vault-status.py --sync-sources
python3 /tmp/vault-toolkit/scripts/wp-publish-draft.py exports/<bestand>.md --type knowledgebase ...
```

Scripts lezen/schrijven relatief aan de **current working directory**
(`os.getcwd()`), niet aan hun eigen locatie — zo werkt één checkout van dit
repo voor elke vault zonder aanpassing.

## Scripts

| Script | Doel |
|---|---|
| `epub-cover.py` | Cover-afbeelding uit een EPUB halen → `exports/covers/` |
| `md2pdf.py` | Export-markdown naar huisstijl-PDF (A4, tabellen, callouts, inline afbeeldingen) |
| `vault-status.py` | Genereert `wiki/status.md` (ingest-achterstand, linkhygiëne, drift) |
| `wp-publish-draft.py` | Publiceert een export als WordPress-concept (Knowledge Base e.d.) |
| `wp-book-showcase.py` | Publiceert een boek naar de Book Showcase-plugin |
| `wp-upload-cover.py` | Upload een cover naar de WP-mediabibliotheek, print de publieke URL |

## WordPress-credentials

`wp-publish-draft.py` (en de scripts die het hergebruiken) lezen credentials
in deze volgorde:

1. `$VAULT_WP_ENV` — expliciet pad naar een env-bestand, indien gezet.
2. `~/.config/<vaultnaam>/wp.env` — `vaultnaam` = lowercase basename van de
   cwd op het moment dat je het script draait (dus `~/.config/histvault/wp.env`
   als je in `HistVault/` staat, `~/.config/claudevault/wp.env` in
   `ClaudeVault/`, enzovoort).
3. Environment variables `WP_URL`, `WP_USER`, `WP_APP_PASSWORD`,
   `WP_GS_BOOKS_LICENSE` — het normale pad in een cloud-sessie zonder lokaal
   credentialbestand.

## Versiebeheer tussen vaults

Elke vault kloont dit repo **vers bij elke dagelijkse run** (geen submodule,
geen vendoring) — zo loopt geen enkele vault ooit op een verouderde kopie.
Wijzig je een script hier, dan pakt de volgende geplande run van alle vaults
de wijziging automatisch op.
