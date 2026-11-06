# Tests

Regressietests voor de scripts in `scripts/` en voor het launchd-schema in
`launchd/`. Draai ze na elke wijziging daar:

```bash
tests/run-tests.sh        # samenvatting per suite
tests/run-tests.sh -v     # met alle checks erbij
```

Alles is zelfstandig: geen netwerk nodig (op één onschuldige uitzondering na,
zie hieronder), geen credentials, en geen van de echte vaults wordt aangeraakt.
Elke suite bouwt zijn eigen wegwerpomgeving in `/tmp` en ruimt die weer op —
behalve `test_launchd.sh`, dat niets neerzet en alleen de bestanden in
`launchd/` leest.

## De suites

| Suite | Bewaakt | Checks |
|---|---|---|
| `test_link_hygiene.py` | de link-sanitizer, duplicaatwacht, index-update en de boekhouding van de uitvoerstappen in `gemini-book-runner.py` | 39 |
| `test_github_sync.sh` | auto-commit, secret-rem, rebase-herstel, dry-run en de inbox-stap van `daily-github-sync.sh` | 51 |
| `test_store_id.sh` | de store-ID-controle in `sync-blob-inbox.py` | 15 |
| `test_automation_check.sh` | de verouderings- én uitkomstcheck op geplande taken in `vault-status.py` | 29 |
| `test_launchd.sh` | het schema en de wrappers in `launchd/`, en de dekking van `install.sh` | 75 |
| `test_tls.sh` | dat geen script certificaatcontrole omzeilt, en dat `vaultenv.tls_context()` echt verifieert | 15 |
| `test_book_enrichment.py` | omslag, tags, Persoon-objecten en boekkenmerken bij een boekpublicatie | 31 |

`fixture_vault.py` is geen suite maar het bouwsteentje eronder: het zet een
synthetische vault met een handvol bekende pagina's neer.

## Waarom deze, en niet iets anders

Ze bewaken allemaal code die **ongevraagd en zonder toezicht** draait, en waar
een stille fout duur is:

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
- De **verouderings- en uitkomstcheck** is de vangnetlaag onder alle drie.
  Launchd haalt een gemiste slag in als de Mac uit stond, dus "heeft lang niet
  gedraaid" was het eerste signaal: op de dag dat die check werd toegevoegd stond
  de websitecontrole al 37 uur stil zonder dat iets dat liet zien. Maar een run
  die *faalt* schrijft óók in zijn log, dus een verse mtime betekende nog niets —
  op 6 nov 2026 weigerde de sync twee repo's en bleef de taak groen. Nu wordt het
  laatste runblok gelezen. De lastige kant daarvan is de valse positief: de
  boekentaak logt `!! CLI Subscription Limit` en slaagt dan alsnog via de
  Gemini-fallback, dus het slotwoord van een run (`exit=`, een eindregel met
  aantallen, `GESLAAGD`) gaat vóór geschreeuw onderweg. Beide kanten staan in de
  tests: handled-en-geslaagd blijft groen, hetzelfde log met `exit=1` wordt rood.
- De **certificaatcontrole** is de enige suite die een *verbod* toetst in plaats
  van gedrag. Tot 6 nov 2026 gebruikten vier scripts
  `ssl._create_unverified_context()`, twee ervan als stille terugval bij élke
  uitzondering, waardoor bij iedere API-aanroep een bearer-token de deur uit
  ging zonder dat het certificaat werd gecontroleerd. Dat is precies het soort
  regressie dat niemand opmerkt, want alles blijft werken — alleen onveilig.
  De scan tokeniseert de bestanden in plaats van te grepen, omdat `vaultenv.py`
  en `capacities-check.py` die namen in hun docstring noemen om uit te leggen
  waarom ze er niet meer staan.
- De **boekverrijking** faalde niet, hij deed te weinig — het lastigste geval om
  op te merken. `capacities-push.py` en `wp-book-showcase.py` konden een omslag,
  ISBN en uitgever al aan, maar de runner gaf ze niet mee, en tags en
  Persoon-objecten bestonden in het push-script helemaal niet. Het resultaat was
  een Boek-object met lege `coverImage`, lege `tags` en één van de twee auteurs,
  zonder één foutmelding. Deze suite legt de bedoelde uitkomst vast: welke
  payload er naar `PATCH /object/markdown` gaat, dat een bestaande persoon wordt
  gekoppeld en een onbekende aangemaakt, en dat `book-cover.py` ook voor een PDF
  een omslag oplevert.
- Het **launchd-schema** is de laag waar al die taken aan hangen. De reden dat de
  websitecontrole 37 uur stil stond, was een logpad in iCloud: launchd kan het
  proces dan niet starten en faalt met `EX_CONFIG 78` — geen regel in het log,
  geen melding. Die ene eigenschap is nu een test, net als de wrappers die naar
  een script in git moeten wijzen in plaats van naar `~/.config`.

## Drie keuzes die uitleg verdienen

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

**`test_launchd.sh` toetst de echte bestanden.** De uitzondering op de regel
hierboven, en wel met opzet: bij de andere suites is de *logica* het testobject
en de vault toevallige invoer, hier is de *inhoud* van de plists zelf het
testobject. Een fixture-plist zou niets zeggen over de taak die om 03:00 draait.
De suite kijkt daarbij naar wat er in `launchd/` staat, niet naar wat er op het
systeem geladen is — of die twee nog gelijk zijn, is de vraag van
`launchd/install.sh --check` en niet van een test. Wel geldt één aanname: deze
repo staat naast de vaults in dezelfde werkruimte, want anders valt het doelpad
van een wrapper daarbuiten.

## Een suite toevoegen

Zet het bestand in deze map, laat elke geslaagde check `ok   <naam>` printen en
exit 1 bij een fout, en voeg één `draai`-regel toe in `run-tests.sh`. Quote de
paden: dit project staat in iCloud Drive onder "Mobile Documents", en een
ongequoteerd pad valt op die spatie uiteen.
