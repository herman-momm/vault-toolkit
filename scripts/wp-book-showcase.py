#!/usr/bin/env python3
"""Push een boek naar de Book Showcase-plugin (gs-amz-books Pro) op dagzoom.nl.

Gebruik:
  python3 scripts/wp-book-showcase.py <export.md> --author "Adam Tooze" \
      [--title "..."] [--isbn ...] [--publisher "..."] [--year 2014]

De volledige export (minus de H1) wordt als HTML in het description-veld gezet.
Credentials: zie wp-publish-draft.py (zelfde load_env(), hier hergebruikt).
"""
import sys, os, re, json, argparse, tempfile, subprocess, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))

# hergebruik de markdown->HTML-converter én de credential-loading uit het draft-script
_spec = importlib.util.spec_from_file_location("wpdraft", os.path.join(HERE, "wp-publish-draft.py"))
_wp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_wp)
md_to_html = _wp.md_to_html
load_env = _wp.load_env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mdfile")
    ap.add_argument("--title", default=None)
    ap.add_argument("--author", required=True)
    ap.add_argument("--isbn", default="")
    ap.add_argument("--asin", default="")
    ap.add_argument("--cover", default="", help="publieke URL van de cover-afbeelding")
    ap.add_argument("--cover-file", dest="cover_file", default="",
                    help="pad naar een cover-afbeelding; wordt geupload en als cover meegestuurd")
    ap.add_argument("--publisher", default="")
    ap.add_argument("--year", default="")
    args = ap.parse_args()

    md = open(args.mdfile, encoding="utf-8").read()
    title = args.title
    if not title:
        m = re.search(r"^#\s+(.*)$", md, re.M)
        title = m.group(1).strip() if m else os.path.basename(args.mdfile)
    body_md = re.sub(r"^#\s+.*$", "", md, count=1, flags=re.M).strip()
    description_html = md_to_html(body_md)

    env = load_env()
    url = env["WP_URL"].rstrip("/")
    user = env["WP_USER"]
    pw = env["WP_APP_PASSWORD"].replace(" ", "")
    lic = env["WP_GS_BOOKS_LICENSE"]

    # Cover-bestand uploaden en als cover-URL gebruiken
    if args.cover_file and not args.cover and os.path.exists(args.cover_file):
        slug = re.sub(r"[^A-Za-z0-9.-]+", "-", os.path.basename(args.cover_file))
        _mid, murl = _wp.upload_media(args.cover_file, url, user, pw, slug)
        if murl:
            args.cover = murl

    payload = {
        "license_key": lic,
        "title": title,
        "author": args.author,
        "description": description_html,
        "isbn": args.isbn,
        "asin": args.asin,
        "publisher": args.publisher,
        "year": args.year,
    }
    if args.cover:
        # meerdere mogelijke veldnamen; de plugin negeert onbekende
        payload["cover"] = args.cover
        payload["cover_url"] = args.cover
        payload["image_url"] = args.cover
        payload["image"] = args.cover
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as pf:
        json.dump(payload, pf, ensure_ascii=False)
        payload_path = pf.name
    body_path = payload_path + ".out"
    cfg = (
        f'url = "{url}/wp-json/gs-amz-books/v1/book"\n'
        f'user = "{user}:{pw}"\n'
        'request = "POST"\n'
        'header = "Content-Type: application/json"\n'
        f'header = "X-GS-BOOKS-LICENSE-KEY: {lic}"\n'
        f'data-binary = "@{payload_path}"\n'
    )
    try:
        proc = subprocess.run(
            ["curl", "-sS", "-m", "45", "-K", "-", "-o", body_path, "-w", "%{http_code}"],
            input=cfg, text=True, capture_output=True,
        )
        code = proc.stdout.strip()
        raw = open(body_path, encoding="utf-8").read() if os.path.exists(body_path) else ""
    finally:
        for p in (payload_path, body_path):
            try:
                os.remove(p)
            except OSError:
                pass

    if code not in ("200", "201"):
        sys.stderr.write(f"FOUT http={code}: {raw[:600]}\n")
        sys.exit(1)
    res = json.loads(raw)
    pid = res.get("post_id")
    print(f"OK: Book Showcase-entry aangemaakt (post_id={pid}, beschrijving {len(description_html)} tekens HTML)")
    print(f"Bewerken: {url}/wp-admin/post.php?post={pid}&action=edit")


if __name__ == "__main__":
    main()
