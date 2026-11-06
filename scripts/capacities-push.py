#!/usr/bin/env python3
"""
capacities-push.py — Universele Capacities API bridge.
Ondersteunt Boek-aanmaak met Omslagafbeelding, Auteursinformatie (Wikipedia bio + foto)
en Daily Note updates.
Leest token en space-id via vaultenv uit capacities.env (per-vault override,
anders de gedeelde configmap) of uit de environment variables
CAPACITIES_TOKEN / CAPACITIES_SPACE_ID.
"""
import sys, os, re, json, argparse, urllib.request, urllib.parse, ssl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultenv

BASE_URL = "https://api.capacities.io"
KEYS = ("CAPACITIES_TOKEN", "CAPACITIES_SPACE_ID")
ENV_PATH = vaultenv.config_path("capacities.env")


def load_env():
    cfg = vaultenv.load_config("capacities.env", KEYS)
    return cfg.get("CAPACITIES_TOKEN"), cfg.get("CAPACITIES_SPACE_ID")


def api_request(endpoint, data=None, method="GET", token=None):
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "vault-toolkit/1.0"
    }
    payload = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    # Mét certificaatcontrole: hier gaat een bearer-token de deur uit.
    ctx = vaultenv.tls_context()
    with urllib.request.urlopen(req, context=ctx) as resp:
        res_body = resp.read().decode("utf-8")
        return json.loads(res_body) if res_body else {}


def fetch_wikipedia_author(author_name: str):
    """Haalt bio, foto-URL en Wikipedia-pagina op voor een auteur."""
    clean_name = re.sub(r'\b(red\.|ed\.|et al\.)\b', '', author_name, flags=re.I).strip()
    if ' en ' in clean_name:
        names = [n.strip() for n in clean_name.split(' en ')]
    elif ',' in clean_name:
        names = [n.strip() for n in clean_name.split(',')]
    else:
        names = [clean_name]

    results = []
    ctx = vaultenv.tls_context()
    for name in names:
        if not name or len(name) < 3:
            continue
        url = f"https://nl.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(name)}"
        req = urllib.request.Request(url, headers={"User-Agent": "vault-toolkit/1.0"})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
                if data.get("type") == "standard":
                    results.append({
                        "name": data.get("title", name),
                        "bio": data.get("extract", ""),
                        "photo": data.get("thumbnail", {}).get("source"),
                        "link": data.get("content_urls", {}).get("desktop", {}).get("page")
                    })
        except Exception:
            pass
    return results


def post_daily_note(markdown: str, token: str, space_id: str):
    endpoint = f"/blocks/daily-note/append?spaceId={space_id}"
    return api_request(endpoint, {"noTimeStamp": True, "markdown": markdown}, method="POST", token=token)


# Structuur-id's van deze space. Het Persoon-type is hetzelfde dat
# fill-capacities-photos.py gebruikt.
PERSON_STRUCTURE_ID = "73abbe4c-adf1-4443-8cb2-20c34b5c029c"

# Toevoegingen achter een auteursnaam die geen deel van de naam zijn.
_ROLSUFFIX = re.compile(
    r"\s*\((?:red\.?|ed\.?|eds\.?|samenst\.?|vert\.?)\)\s*$", re.I)
# "(met bijdragen van X)" bevat een échte auteur: maak er een scheidingsteken van.
_BIJDRAGEN = re.compile(r"\s*\((?:met\s+)?bijdragen\s+van\s+([^)]+)\)", re.I)


def split_authors(namen: str):
    """Splits een auteursveld in losse namen.

    De runner levert één string aan ("David Kertai & Jona Lendering", soms
    "A, B en C" of "X (met bijdragen van Y)"). Eén string betekende tot nu toe
    één markdown-regel; voor Persoon-objecten is een echte lijst nodig.
    """
    if not namen:
        return []
    tekst = _BIJDRAGEN.sub(r", \1", namen)
    delen = re.split(r"\s*(?:&|;|/|,| en )\s*", tekst)
    uit = []
    for d in delen:
        d = _ROLSUFFIX.sub("", d)
        # "e.a." / "et al." horen niet bij de naam. Dit gebeurt per deel en niet
        # op de hele string, want de punten maken woordgrenzen onbetrouwbaar.
        d = re.sub(r"\s+(?:e\.?\s*a\.?|et\s+al\.?)\s*$", "", d, flags=re.I)
        d = d.strip(" .,")
        # Een "naam" van één teken of puur cijfers is geen naam.
        if len(d) > 2 and not d.isdigit() and d not in uit:
            uit.append(d)
    return uit


def find_or_create_person(naam: str, token: str, space_id: str):
    """Zoek een Persoon-object op titel; maak het aan als het niet bestaat.

    Retourneert de titel zoals die in Capacities staat (nodig voor de
    `[[wikilink]]`-vorm), of None als het misging. Bewust zoeken-dan-aanmaken:
    de API documenteert niet dat een wikilink naar een onbekende titel vanzelf
    een object maakt, en een stil niet-gelegde link is precies wat we hier aan
    het repareren zijn.
    """
    try:
        res = api_request("/objects/search",
                          {"query": naam, "structureIds": [PERSON_STRUCTURE_ID]},
                          method="POST", token=token)
    except Exception as e:
        print(f"  ! zoeken naar persoon '{naam}' mislukte: {e}", file=sys.stderr)
        return None

    treffers = res.get("objects") or res.get("results") or []
    for obj in treffers:
        titel = (obj.get("title") or "").strip()
        if titel.lower() == naam.strip().lower():
            print(f"  · auteur gekoppeld aan bestaand Persoon-object: {titel}")
            return titel

    try:
        nieuw = api_request(
            "/object",
            {"structureId": PERSON_STRUCTURE_ID,
             "properties": {"title": {"type": "title", "title": {"value": naam}}}},
            method="POST", token=token)
    except Exception as e:
        print(f"  ! aanmaken van persoon '{naam}' mislukte: {e}", file=sys.stderr)
        return None
    if nieuw.get("id"):
        print(f"  · nieuw Persoon-object aangemaakt: {naam}")
        return naam
    return None


def update_book_properties(obj_id, tags, cover_url, author_titles, token):
    """Zet tags, omslag en auteurs via PATCH /object/markdown.

    Deze drie zijn eigenschappen van het Boek-type en kunnen niet in de body:
    `tags` is een kommalijst met namen, `coverImage` neemt een http(s)-URL en
    importeert die als MediaImage, en `auteur` neemt `[[Titel]]`-wikilinks en
    **vervangt** de hele lijst — dus alle auteurs in één keer meesturen.
    """
    regels = []
    if tags:
        regels.append("tags: " + ", ".join(tags))
    if cover_url and cover_url.startswith(("http://", "https://")):
        regels.append("coverImage: " + cover_url)
    if author_titles:
        regels.append("auteur: " + ", ".join(f"[[{t}]]" for t in author_titles))
    if not regels:
        return False

    md = "---\n" + "\n".join(regels) + "\n---\n"
    try:
        api_request("/object/markdown", {"id": obj_id, "markdown": md},
                    method="PATCH", token=token)
    except Exception as e:
        print(f"  ! eigenschappen zetten mislukte: {e}", file=sys.stderr)
        return False
    print("Eigenschappen gezet: " + " · ".join(regels))
    return True


def create_book(title: str, description: str, year: int, markdown_file: str, cover_url: str, author_names: str, token: str, space_id: str, tags=None):
    body = {
        "structureId": "b7b81235-c1dd-4261-b742-2edaa4ed7951",
        "properties": {
            "title": {"type": "title", "title": {"value": title}},
            "description": {"type": "text", "text": {"value": description or author_names or ""}},
        }
    }
    if year:
        body["properties"]["c2e151f9-2e9b-4add-b777-a864036e3bff"] = {"type": "number", "number": {"value": year}}

    res = api_request(f"/object?spaceId={space_id}", body, method="POST", token=token)
    obj_id = res.get("id")
    print(f"Boek aangemaakt in Capacities (ID: {obj_id})")

    # Eigenschappen die niet via de aanmaak-body kunnen: tags, omslag, auteurs.
    auteur_titels = []
    for naam in split_authors(author_names):
        titel = find_or_create_person(naam, token, space_id)
        if titel:
            auteur_titels.append(titel)
    update_book_properties(obj_id, tags or [], cover_url, auteur_titels, token)

    header_blocks = []

    # De omslag hoort in de `coverImage`-eigenschap. Alleen als dat niet kon —
    # een lokaal pad in plaats van een URL — valt hij terug op een plaatje in de
    # body, zodat hij tenminste ergens zichtbaar is.
    if cover_url and not cover_url.startswith(("http://", "https://")):
        header_blocks.append(f"![Omslag]({cover_url})\n")

    authors_info = fetch_wikipedia_author(author_names or description)
    if authors_info:
        header_blocks.append("## 👤 Auteur(s)\n")
        for a in authors_info:
            header_blocks.append(f"### {a['name']}")
            if a.get('photo'):
                header_blocks.append(f"![{a['name']}]({a['photo']})")
            if a.get('bio'):
                header_blocks.append(f"*{a['bio']}*")
            if a.get('link'):
                header_blocks.append(f"🔗 [Wikipedia profiel]({a['link']})\n")

    body_md = ""
    if markdown_file and os.path.exists(markdown_file):
        with open(markdown_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        body_md = "".join(lines[1:] if lines and lines[0].startswith("# ") else lines).strip()

    full_content = "\n\n".join([b for b in header_blocks if b]) + ("\n\n---\n\n" + body_md if body_md else "")

    if full_content.strip():
        api_request("/blocks/append", {"id": obj_id, "markdown": full_content.strip()}, method="POST", token=token)
        # Benoem wat er daadwerkelijk in de body staat. Deze regel meldde eerder
        # onvoorwaardelijk "Omslag, auteurspersoon-gegevens en samenvattings-
        # blokken", ook als er geen omslag en geen auteursinfo was.
        delen = []
        if header_blocks and header_blocks[0].startswith("![Omslag]"):
            delen.append("omslag (in de body)")
        if authors_info:
            delen.append(f"{len(authors_info)} auteursprofiel(en) uit Wikipedia")
        if body_md:
            delen.append("samenvattingsblokken")
        print("Toegevoegd aan de body: " + (" · ".join(delen) or "niets"))

    url = f"https://app.capacities.io/{space_id}/{obj_id}"
    print(f"Capacities URL: {url}")
    return obj_id, url



def main():
    token, space_id = load_env()
    if not token or not space_id:
        sys.exit(f"Geen Capacities token of space-id gevonden (gezocht in {ENV_PATH} "
                 "en in de environment variables CAPACITIES_TOKEN/CAPACITIES_SPACE_ID)")

    parser = argparse.ArgumentParser(description="Capacities API tool")
    parser.add_argument("--daily-note", help="Markdown-tekst om aan daily note toe te voegen")
    parser.add_argument("--book-md", help="Pad naar boek-export markdown")
    parser.add_argument("--title", help="Boektitel")
    parser.add_argument("--desc", help="Korte beschrijving")
    parser.add_argument("--year", type=int, help="Jaar van verschijnen")
    parser.add_argument("--author", help="Naam van auteur(s)")
    parser.add_argument("--cover", help="Omslagafbeelding: http(s)-URL (wordt de "
                                        "coverImage-eigenschap) of lokaal pad "
                                        "(belandt als plaatje in de body)")
    parser.add_argument("--tags", help="Kommalijst met tagnamen voor het Boek-object")

    args = parser.parse_args()

    if args.daily_note:
        post_daily_note(args.daily_note, token, space_id)
        print("Daily note bijgewerkt.")
    elif args.book_md and args.title:
        create_book(
            title=args.title,
            description=args.desc or "",
            year=args.year or 0,
            markdown_file=args.book_md,
            cover_url=args.cover or "",
            author_names=args.author or args.desc or "",
            token=token,
            space_id=space_id,
            tags=[t.strip() for t in (args.tags or "").split(",") if t.strip()],
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
