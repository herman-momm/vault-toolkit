#!/usr/bin/env python3
"""
capacities-push.py — Universele Capacities API bridge.
Ondersteunt Boek-aanmaak met Omslagafbeelding, Auteursinformatie (Wikipedia bio + foto)
en Daily Note updates.
Leest token en space-id uit ~/.config/histvault/capacities.env.
"""
import sys, os, re, json, argparse, urllib.request, urllib.parse, ssl

ENV_PATH = os.path.expanduser("~/.config/histvault/capacities.env")
BASE_URL = "https://api.capacities.io"


def load_env():
    token = os.environ.get("CAPACITIES_TOKEN")
    space_id = os.environ.get("CAPACITIES_SPACE_ID")
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("CAPACITIES_TOKEN="):
                    token = line.split("=", 1)[1].strip("\"'")
                elif line.startswith("CAPACITIES_SPACE_ID="):
                    space_id = line.split("=", 1)[1].strip("\"'")
    return token, space_id


def api_request(endpoint, data=None, method="GET", token=None):
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "vault-toolkit/1.0"
    }
    payload = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    ctx = ssl._create_unverified_context()
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
    ctx = ssl._create_unverified_context()
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


def create_book(title: str, description: str, year: int, markdown_file: str, cover_url: str, author_names: str, token: str, space_id: str):
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

    header_blocks = []

    if cover_url:
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
        print("Omslag, auteurspersoon-gegevens en samenvattingsblokken toegevoegd.")

    url = f"https://app.capacities.io/{space_id}/{obj_id}"
    print(f"Capacities URL: {url}")
    return obj_id, url



def main():
    token, space_id = load_env()
    if not token or not space_id:
        sys.exit("Geen Capacities token of space-id gevonden in ~/.config/histvault/capacities.env")

    parser = argparse.ArgumentParser(description="Capacities API tool")
    parser.add_argument("--daily-note", help="Markdown-tekst om aan daily note toe te voegen")
    parser.add_argument("--book-md", help="Pad naar boek-export markdown")
    parser.add_argument("--title", help="Boektitel")
    parser.add_argument("--desc", help="Korte beschrijving")
    parser.add_argument("--year", type=int, help="Jaar van verschijnen")
    parser.add_argument("--author", help="Naam van auteur(s)")
    parser.add_argument("--cover", help="Omslagafbeelding URL of pad")

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
            space_id=space_id
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
