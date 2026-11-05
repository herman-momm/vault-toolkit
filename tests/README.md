# Tests

Regressietests voor de scripts in `scripts/`. Draai ze na elke wijziging daar:

```bash
tests/run-tests.sh        # samenvatting per suite
tests/run-tests.sh -v     # met alle checks erbij
```

Alles is zelfstandig: geen netwerk nodig (op één onschuldige uitzondering na,
zie hieronder), geen credentials, en geen van de echte vaults wordt aangeraakt.
Elke suite bouwt zijn eigen wegwerpomgeving in `/tmp` en ruimt die weer op.

## De suites

| Suite | Bewaakt | Checks |
|---|---|---|
| `test_link_hygiene.py` | de link-sanitizer, duplicaatwacht en index-update in `gemini-book-runner.py` | 32 |
| `test_github_sync.sh` | auto-commit, secret-rem, rebase-herstel en dry-run van `daily-github-sync.sh` | 32 |
| `test_store_id.sh` | de store-ID-controle in `sync-blob-inbox.py` | 15 |
| `test_automation_check.sh` | de verouderingscheck op geplande taken in `vault-status.py` | 16 |

`fixture_vault.py` is geen suite maar het bouwsteentje eronder: het zet een
synthetische vault met een handvol bekende pagina's neer.

## Waarom deze drie, en niet iets anders

Alle drie bewaken code die **ongevraagd en zonder toezicht** draait, en waar een
stille fout duur is:

- De **boekenrunner** liet op 5 november 2026 in HistVault 52 kapotte links en
  een duplicaat-bronpagina achter. Het model schreef Obsidian-titellinks
  (`[[Doggerland]]`) in plaats van de vaultconventie met pad
  (`[[entities/doggerland]]`) en verzon "Zie ook"-pagina's. De sanitizer die dat
  opvangt is puur tekstwerk met veel randgevallen — precies het soort code dat
  je niet met de hand wilt hertesten.
- De **nachtsync** (launchd, 03:00) commit alles in vijf repo's. Twee kanten zijn
  gevaarlijk: dat er niets ongemerkt blijft staan, én dat een repo met een
  secret in de wijziging wordt geweigerd in plaats van gecommit.
- De **store-ID-controle** voorkomt dat de inbox-sync in de ene vault met het
  token van een andere draait en bestanden in de verkeerde `raw/` zet.
- De **verouderingscheck** is de vangnetlaag onder alle drie: launchd haalt een
  gemiste slag in als de Mac uit stond, maar een run die *faalt* meldde niets.
  Op de dag dat de check werd toegevoegd bleek de websitecontrole al 37 uur stil
  te staan zonder dat iets dat liet zien.

## Twee keuzes die uitleg verdienen

**Synthetische fixtures, geen echte vault.** De eerste versie van de
hygiënetests toetste tegen HistVault en controleerde letterlijk "608 pagina's"
en "172 entiteiten". Die vault groeit elke dag, dus zulke tests gaan vals alarm
geven zodra iemand een bron verwerkt. `fixture_vault.py` legt in plaats daarvan
een kleine, bekende vault neer; de verwachte tellingen staan als constante in
dat bestand.

**Eén API-aanroep blijft erin.** In `test_store_id.sh` lopen de vier checks met
een *kloppend* neptoken dóór naar de Vercel API, die dan voorspelbaar faalt met
`store_not_found`. Dat is onschuldig (geen authenticatie, geen data) en de
assertions kijken alleen naar de regel die het script vóór die aanroep print, dus
offline gelden ze net zo goed. Het volledige pad met een echt token — downloaden
en daarna uit de inbox verwijderen — wordt hierdoor níet getoetst; daar is een
echte store voor nodig.

## Een suite toevoegen

Zet het bestand in deze map, laat elke geslaagde check `ok   <naam>` printen en
exit 1 bij een fout, en voeg één `draai`-regel toe in `run-tests.sh`. Quote de
paden: dit project staat in iCloud Drive onder "Mobile Documents", en een
ongequoteerd pad valt op die spatie uiteen.
