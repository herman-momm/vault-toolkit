#!/usr/bin/env python3
"""Haal nieuwe bestanden uit de Vercel Blob-inbox naar raw/.

De /upload-pagina in de web-app zet geuploade bestanden en opgehaalde
webpagina's in de Blob-store onder het pad `inbox/`. Dit script haalt die
bestanden op, slaat ze op in raw/ en verwijdert ze daarna uit de inbox zodat
ze niet dubbel verwerkt worden.

Gebruik:
  python3 scripts/sync-blob-inbox.py            # haalt alles op
  python3 scripts/sync-blob-inbox.py --dry-run   # toont alleen wat er klaarstaat

Het token wordt gezocht in deze volgorde (en het script meldt welke bron het
gebruikte):
  1. --token
  2. de vault-specifieke env-var: BLOB_READ_WRITE_TOKEN_HIST voor HistVault,
     _GREEN voor GroeneVault, de generieke naam voor ClaudeVault — zodat drie
     vaults in één omgeving elkaars store niet raken
  3. BLOB_READ_WRITE_TOKEN (generiek)
  4. `web/.env.local` van de vault zelf — lokaal vaak het enige exemplaar,
     omdat de tokens in het Vercel-project per vault leven
BLOB_STORE_ID (zelfde zoekorde, zelfde suffixen) is optioneel maar aanbevolen:
als die gezet is, wordt gecontroleerd dat het token ook echt bij die store
hoort. Dat voorkomt dat dit script met het token van een andere vault draait
en stilletjes "inbox is leeg" rapporteert — of, erger, de bestanden van de ene
vault in de raw/ van een andere zet.

Draai dit script vanuit de vaultroot (of zet $VAULT_ROOT): de doelmap is
<vaultroot>/raw.
"""
import sys
import os
import re
import json
import argparse
import subprocess
import html
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultenv

# De vaultroot komt uit de werkmap (of $VAULT_ROOT), niet uit de locatie van dit
# script: als gedeelde toolkit-kopie zou dat laatste naar vault-toolkit/raw wijzen.
VAULT_DIR = vaultenv.vault_root(marker="raw")
RAW_DIR = os.path.join(VAULT_DIR, "raw")
API_BASE = "https://vercel.com/api/blob"
API_VERSION = "13"
INBOX_PREFIX = "inbox/"
TOKEN_ENV_VAR = "BLOB_READ_WRITE_TOKEN"
STORE_ID_ENV_VAR = "BLOB_STORE_ID"


class SimpleHTMLToMarkdown(HTMLParser):
    def __init__(self):
        super().__init__()
        self.output = []
        self.ignored_depth = 0
        self.ignored_tags = {
            "script", "style", "svg", "noscript", "nav",
            "footer", "header", "aside", "form"
        }
        self.link_stack = []
        self.heading_level = 0
        self.bold_depth = 0
        self.italic_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag in self.ignored_tags:
            self.ignored_depth += 1
            return
        cls = attrs_dict.get("class", "").lower()
        elem_id = attrs_dict.get("id", "").lower()
        if any(w in cls or w in elem_id for w in ["cookie", "consent", "privacy"]):
            self.ignored_depth += 1
            return
        if self.ignored_depth > 0:
            if tag in ("div", "section"): self.ignored_depth += 1
            return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading_level = int(tag[1])
            self.output.append(f"\n\n{'#' * self.heading_level} ")
        elif tag == "p":
            self.output.append("\n\n")
        elif tag == "br":
            self.output.append("\n")
        elif tag == "blockquote":
            self.output.append("\n\n> ")
        elif tag in ("strong", "b"):
            self.output.append("**")
            self.bold_depth += 1
        elif tag in ("em", "i"):
            self.output.append("*")
            self.italic_depth += 1
        elif tag == "a":
            href = attrs_dict.get("href", "").strip()
            self.link_stack.append(href)
            self.output.append("[")
        elif tag == "li":
            self.output.append("\n- ")
        elif tag == "hr":
            self.output.append("\n\n---\n\n")

    def handle_endtag(self, tag):
        if self.ignored_depth > 0:
            if tag in self.ignored_tags or tag in ("div", "section"):
                self.ignored_depth -= 1
            return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading_level = 0
            self.output.append("\n\n")
        elif tag == "p":
            self.output.append("\n\n")
        elif tag in ("strong", "b") and self.bold_depth > 0:
            self.output.append("**")
            self.bold_depth -= 1
        elif tag in ("em", "i") and self.italic_depth > 0:
            self.output.append("*")
            self.italic_depth -= 1
        elif tag == "a" and self.link_stack:
            href = self.link_stack.pop()
            if href and not href.startswith("javascript:"):
                self.output.append(f"]({href})")
            else:
                self.output.append("]")

    def handle_data(self, data):
        if self.ignored_depth > 0:
            return
        t = re.sub(r"[ \t]+", " ", data)
        if t: self.output.append(t)


def clean_html_file(html_path: str) -> str:
    """Strip HTML ballast en sla op als compact .md-bestand (-80% tot -95% tokens)."""
    with open(html_path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()

    source_url = ""
    m = re.search(r"<!--\s*source-url:\s*(https?://[^\s>]+)\s*-->", raw)
    if m: source_url = m.group(1).strip()

    parser = SimpleHTMLToMarkdown()
    parser.feed(raw)
    text = "".join(parser.output)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    header = f"<!-- source-url: {source_url} -->\n\n" if source_url else ""
    md_content = header + text + "\n"

    md_path = os.path.splitext(html_path)[0] + ".md"
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md_content)

    orig_size = len(raw.encode("utf-8"))
    clean_size = len(md_content.encode("utf-8"))
    saved_pct = round((1 - clean_size / orig_size) * 100, 1) if orig_size > 0 else 0

    os.remove(html_path)
    print(f" -> gestript naar .md ({orig_size:,}b -> {clean_size:,}b, -{saved_pct}%)", end="")
    return os.path.basename(md_path)



def token_store_id(token):
    """Leidt de store-ID af uit een vercel_blob_rw_<id>_<secret>-token."""
    m = re.match(r"^vercel_blob_rw_([^_]+)_", token)
    return f"store_{m.group(1)}" if m else None


def curl_json(args):
    proc = subprocess.run(["curl", "-sS", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"curl mislukt: {proc.stderr}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"Onverwacht antwoord: {proc.stdout}", file=sys.stderr)
        sys.exit(1)


def list_inbox(token):
    blobs = []
    cursor = None
    while True:
        params = f"prefix={INBOX_PREFIX}"
        if cursor:
            params += f"&cursor={cursor}"
        data = curl_json([
            f"{API_BASE}?{params}",
            "-H", f"Authorization: Bearer {token}",
            "-H", f"x-api-version: {API_VERSION}",
        ])
        if "error" in data:
            print(f"Vercel Blob API-fout: {data['error']}", file=sys.stderr)
            sys.exit(1)
        blobs.extend(data.get("blobs", []))
        if not data.get("hasMore"):
            break
        cursor = data.get("cursor")
    return blobs


def delete_blob(token, url):
    proc = subprocess.run(
        [
            "curl", "-sS", "-X", "POST", f"{API_BASE}/delete",
            "-H", f"Authorization: Bearer {token}",
            "-H", f"x-api-version: {API_VERSION}",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"urls": [url]}),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"  Waarschuwing: kon blob niet verwijderen uit inbox: {proc.stderr}", file=sys.stderr)


def download(url, dest_path, token):
    proc = subprocess.run(
        ["curl", "-sS", "-f", "-H", f"Authorization: Bearer {token}", "-o", dest_path, url],
        capture_output=True, text=True,
    )
    return proc.returncode == 0


def resolve_secret(explicit, base_name):
    """Zoek een Blob-sleutel voor DEZE vault en meld waar hij vandaan komt.

    Volgorde: expliciet meegegeven waarde, dan de vault-specifieke env-var
    (`..._HIST`/`..._GREEN`), dan de generieke env-var, dan `web/.env.local`
    van de vault zelf — lokaal vaak het enige exemplaar, omdat de tokens in
    het Vercel-project per vault leven. Retourneert (waarde, herkomst).
    """
    if explicit:
        return explicit, "command line"
    for name in vaultenv.suffixed_names(base_name):
        if os.environ.get(name):
            return os.environ[name], f"omgeving (${name})"
    local = vaultenv.vault_dotenv()
    if local.get(base_name):
        return local[base_name], f"{os.path.join('web', '.env.local')} van {vaultenv.vault_name()}"
    return None, ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="alleen tonen, niets downloaden of verwijderen")
    parser.add_argument("--no-clean", action="store_true", help="sla automatische HTML-naar-Markdown conversie over")
    parser.add_argument("--token", help=f"Blob read-write token voor DEZE vault's store (overschrijft {TOKEN_ENV_VAR})")
    parser.add_argument("--store-id", help=f"Verwachte store-ID, als sanity-check tegen het token (overschrijft {STORE_ID_ENV_VAR})")
    args = parser.parse_args()

    token, token_src = resolve_secret(args.token, TOKEN_ENV_VAR)
    if not token:
        names = " of ".join(vaultenv.suffixed_names(TOKEN_ENV_VAR))
        print(f"Geen token gevonden voor {vaultenv.vault_name()}: geef --token mee, zet "
              f"{names} in de omgeving, of zet {TOKEN_ENV_VAR} in {os.path.join('web', '.env.local')} "
              f"van deze vault.", file=sys.stderr)
        sys.exit(1)
    print(f"Token uit: {token_src}")

    expected_store_id, _ = resolve_secret(args.store_id, STORE_ID_ENV_VAR)
    if expected_store_id:
        actual_store_id = token_store_id(token)
        if actual_store_id and actual_store_id != expected_store_id:
            print(
                f"Store-ID mismatch: het token wijst naar {actual_store_id}, maar "
                f"{STORE_ID_ENV_VAR} is {expected_store_id}. Dit is waarschijnlijk het "
                f"token van een andere vault — sync afgebroken.",
                file=sys.stderr,
            )
            sys.exit(1)

    blobs = list_inbox(token)
    if not blobs:
        print("Inbox is leeg — niets te synchroniseren.")
        return

    os.makedirs(RAW_DIR, exist_ok=True)
    existing = set(os.listdir(RAW_DIR))

    new_files = []
    for blob in blobs:
        pathname = blob["pathname"]
        name = pathname[len(INBOX_PREFIX):] if pathname.startswith(INBOX_PREFIX) else pathname
        if not name:
            continue
        # De upload-pagina prefixt met een epoch-ms timestamp (uniciteit i.p.v.
        # Vercel's eigen addRandomSuffix) zodat de originele bestandsnaam intact blijft.
        name = re.sub(r"^\d{10,13}-", "", name)
        if name in existing:
            print(f"- {name}: al aanwezig in raw/, sla over (niet verwijderd uit inbox)")
            continue
        new_files.append((name, blob))

    if not new_files:
        print("Niets nieuws in de inbox.")
        return

    print(f"{len(new_files)} nieuw bestand(en) gevonden in de inbox:")
    for name, _ in new_files:
        print(f"  - {name}")

    if args.dry_run:
        print("\n--dry-run: niets gedownload of verwijderd.")
        return

    downloaded_names = []
    for name, blob in new_files:
        dest = os.path.join(RAW_DIR, name)
        print(f"Downloaden: {name} ...", end=" ")
        if download(blob["downloadUrl"], dest, token):
            print("ok", end="")
            delete_blob(token, blob["url"])
            if not args.no_clean and dest.endswith((".html", ".htm")):
                new_name = clean_html_file(dest)
                downloaded_names.append(new_name)
            else:
                downloaded_names.append(name)
            print()
        else:
            print("MISLUKT (bestand niet verwijderd uit inbox, probeer later opnieuw)")

    print(f"\nKlaar. Nieuwe bestanden staan in raw/: {', '.join(downloaded_names)}")
    print("Vergeet niet de normale ingest-workflow te volgen (zie CLAUDE.md) en te committen.")


if __name__ == "__main__":
    main()
