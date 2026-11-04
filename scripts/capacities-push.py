#!/usr/bin/env python3
"""
capacities-push.py — Universele Capacities API bridge.
Leest token en space-id uit ~/.config/histvault/capacities.env.
"""
import sys, os, json, argparse, urllib.request, ssl

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


def post_daily_note(markdown: str, token: str, space_id: str):
    endpoint = f"/blocks/daily-note/append?spaceId={space_id}"
    return api_request(endpoint, {"noTimeStamp": True, "markdown": markdown}, method="POST", token=token)


def create_book(title: str, description: str, year: int, markdown_file: str, token: str, space_id: str):
    # 1. Maak Boek object
    body = {
        "structureId": "b7b81235-c1dd-4261-b742-2edaa4ed7951",
        "properties": {
            "title": {"type": "title", "title": {"value": title}},
            "description": {"type": "text", "text": {"value": description}},
        }
    }
    if year:
        body["properties"]["c2e151f9-2e9b-4add-b777-a864036e3bff"] = {"type": "number", "number": {"value": year}}

    res = api_request(f"/object?spaceId={space_id}", body, method="POST", token=token)
    obj_id = res.get("id")
    print(f"Boek aangemaakt (ID: {obj_id})")

    # 2. Append markdown body
    if markdown_file and os.path.exists(markdown_file):
        with open(markdown_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        body_md = "".join(lines[1:] if lines and lines[0].startswith("# ") else lines).strip()
        api_request("/blocks/append", {"id": obj_id, "markdown": body_md}, method="POST", token=token)
        print("Samenvatting-blocks toegevoegd aan boek.")

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

    args = parser.parse_args()

    if args.daily_note:
        post_daily_note(args.daily_note, token, space_id)
        print("Daily note bijgewerkt.")
    elif args.book_md and args.title:
        create_book(args.title, args.desc or "", args.year or 0, args.book_md, token, space_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
