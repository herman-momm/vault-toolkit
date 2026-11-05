#!/usr/bin/env python3
"""
gemini-book-runner.py — Geautomatiseerde boeksamenvatting via Gemini 3.8 Flash.
Verwerkt PDF/EPUB boeken uit book-queue.txt in 1 snelle, complete pass.
"""
import sys
import os
import re
import json
import ssl
import time
import datetime
import subprocess
import urllib.request
import urllib.error

try:
    import pymupdf as fitz
except ImportError:
    import fitz

def load_env(vault_dir):
    env_file = os.path.expanduser("~/.config/histvault/claude.env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

def extract_text_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    full_text = []
    for i, page in enumerate(doc):
        t = page.get_text()
        if t.strip():
            full_text.append(f"--- PAGINA {i+1} ---\n{t.strip()}")
    return "\n\n".join(full_text)

def extract_text_from_epub(epub_path):
    import zipfile
    full_text = []
    with zipfile.ZipFile(epub_path, 'r') as z:
        for name in z.namelist():
            if name.endswith(('.html', '.xhtml', '.htm')):
                content = z.read(name).decode('utf-8', errors='ignore')
                text = re.sub(r'<[^>]+>', ' ', content)
                text = re.sub(r'\s+', ' ', text).strip()
                if text:
                    full_text.append(text)
    return "\n\n".join(full_text)

def call_gemini(prompt, api_key, model="gemini-3.8-flash"):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")

    try:
        ssl_context = ssl.create_default_context()
        response_obj = urllib.request.urlopen(req, timeout=180, context=ssl_context)
    except Exception:
        ssl_context = ssl._create_unverified_context()
        response_obj = urllib.request.urlopen(req, timeout=180, context=ssl_context)

    with response_obj as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        candidates = res_data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)
        else:
            raise RuntimeError("Geen respons ontvangen van Gemini API")

def main():
    vault_dir = os.getcwd()
    load_env(vault_dir)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY niet ingesteld.", file=sys.stderr)
        sys.exit(1)

    queue_file = os.path.join(vault_dir, "scripts", "book-queue.txt")
    if not os.path.exists(queue_file):
        print(f"Wachtrij-bestand niet gevonden: {queue_file}", file=sys.stderr)
        sys.exit(0)

    with open(queue_file, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    if not lines:
        print("Wachtrij is leeg — niets te doen.")
        sys.exit(0)

    book_filename = lines[0]
    book_path = os.path.join(vault_dir, "raw", book_filename)

    if not os.path.exists(book_path):
        print(f"Boekbestand niet gevonden in raw/: {book_path}", file=sys.stderr)
        sys.exit(1)

    print(f"=== Gemini Book Runner gestart voor: {book_filename} ===")
    today = datetime.date.today().strftime("%Y-%m-%d")

    if book_filename.lower().endswith(".pdf"):
        print("Tekst extraheren uit PDF...")
        book_text = extract_text_from_pdf(book_path)
    elif book_filename.lower().endswith((".epub", ".epub3")):
        print("Tekst extraheren uit EPUB...")
        book_text = extract_text_from_epub(book_path)
    else:
        print(f"Onbekende bestandsextensie: {book_filename}", file=sys.stderr)
        sys.exit(1)

    print(f"Tekst geëxtraheerd ({len(book_text)} tekens). Gemini prompt voorbereiden...")

    system_prompt = f"""
Je bent een senior academische analist en vault-beheerder voor een Obsidian/Karpathy-style LLM Wiki kennisbank.
Geef een uitputtende, hoogwaardige analyse en samenvatting van het onderstaande boek.

VANDAAG IS: {today}
BESTANDSNAAM IN RAW: raw/{book_filename}

GEEF JE ANTWOORD IN EXACT DE VOLGENDE JSON STRUCTUUR (zonder markdown codeblocks buiten de JSON, antwoord alleen valide JSON):
{{
  "slug": "kort-kebab-case-slug",
  "title": "Exacte Hoofdtitel van het Boek",
  "author": "Auteur(s)",
  "year": "Jaar van uitgave (YYYY)",
  "publisher": "Uitgeverij",
  "isbn": "ISBN indien gevonden anders leeg",
  "category": "Geschiedenis / Archeologie / etc.",
  "query_markdown": "Volledige inhoud voor wiki/queries/{today}-<slug>-samenvatting.md (in Obsidian Markdown met frontmatter type: query, sources: 0, [[wikilinks]], overzichtstabellen, blockquotes, en ## Zie ook sectie)",
  "export_markdown": "Volledige inhoud voor exports/<slug>-samenvatting.md (verhalende, uitgebreide analyse 1800-2800 woorden, kernthese, hoofdstuk-voor-hoofdstuk opbouw, meerdere tabellen, kernquotes, betekenis. Geen emoji, geen wikilinks in export, PDF-geschikt)",
  "source_markdown": "Volledige inhoud voor wiki/sources/{today}-<slug>.md (bronpagina frontmatter type: source, raw: {book_filename}, title, author, year, publisher, isbn, met korte samenvatting en ## Zie ook link naar querypagina)"
}}

--- INHOUD VAN HET BOEK ---
{book_text[:600000]}
"""

    print("Verzoek versturen naar Gemini 3.8 Flash...")
    res_raw = call_gemini(system_prompt, api_key)

    clean_json_str = res_raw.strip()
    if clean_json_str.startswith("```json"):
        clean_json_str = clean_json_str[7:]
    if clean_json_str.startswith("```"):
        clean_json_str = clean_json_str[3:]
    if clean_json_str.endswith("```"):
        clean_json_str = clean_json_str[:-3]
    clean_json_str = clean_json_str.strip()

    data = json.loads(clean_json_str)

    slug = data.get("slug", "samenvatting").lower()
    slug = re.sub(r'[^a-z0-9\-]', '', slug)

    query_file = os.path.join(vault_dir, "wiki", "queries", f"{today}-{slug}-samenvatting.md")
    export_file = os.path.join(vault_dir, "exports", f"{slug}-samenvatting.md")
    source_file = os.path.join(vault_dir, "wiki", "sources", f"{today}-{slug}.md")

    print(f"Schrijven naar querypagina: {query_file}")
    with open(query_file, "w", encoding="utf-8") as f:
        f.write(data["query_markdown"].strip() + "\n")

    print(f"Schrijven naar exportbestand: {export_file}")
    with open(export_file, "w", encoding="utf-8") as f:
        f.write(data["export_markdown"].strip() + "\n")

    if not os.path.exists(source_file):
        print(f"Schrijven naar bronpagina: {source_file}")
        with open(source_file, "w", encoding="utf-8") as f:
            f.write(data["source_markdown"].strip() + "\n")

    print("PDF bouwen via md2pdf.py...")
    md2pdf_script = os.path.join(vault_dir, "scripts", "md2pdf.py")
    if not os.path.exists(md2pdf_script):
        md2pdf_script = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/LLM-Workspace/vault-toolkit/scripts/md2pdf.py")
    if os.path.exists(md2pdf_script):
        subprocess.run([sys.executable, md2pdf_script, export_file], check=False)

    print("WordPress concept publiceren via wp-publish-draft.py...")
    wp_draft_script = os.path.join(vault_dir, "scripts", "wp-publish-draft.py")
    if not os.path.exists(wp_draft_script):
        wp_draft_script = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/LLM-Workspace/vault-toolkit/scripts/wp-publish-draft.py")
    if os.path.exists(wp_draft_script):
        subprocess.run([sys.executable, wp_draft_script, export_file, "--title", f"{data['title']} ({data['author']})"], check=False)

    print("WordPress Book Showcase publiceren via wp-book-showcase.py...")
    wp_showcase_script = os.path.join(vault_dir, "scripts", "wp-book-showcase.py")
    if not os.path.exists(wp_showcase_script):
        wp_showcase_script = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/LLM-Workspace/vault-toolkit/scripts/wp-book-showcase.py")
    if os.path.exists(wp_showcase_script):
        cmd = [sys.executable, wp_showcase_script, export_file, "--title", data['title'], "--author", data['author']]
        if data.get("year"):
            cmd.extend(["--year", str(data['year'])])
        subprocess.run(cmd, check=False)

    print("Capacities publiceren via capacities-push.py...")
    cap_script = os.path.join(vault_dir, "scripts", "capacities-push.py")
    if not os.path.exists(cap_script):
        cap_script = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/LLM-Workspace/vault-toolkit/scripts/capacities-push.py")
    if os.path.exists(cap_script):
        daily_msg = f"📚 **{data['title']}** — {data['author']} ({data.get('year', '')})\nUitgebreide boeksamenvatting en analyse verwerkt in de vault."
        subprocess.run([sys.executable, cap_script, "--daily-note", daily_msg], check=False)
        cap_cmd = [sys.executable, cap_script, "--book-md", export_file, "--title", data['title'], "--desc", data['author']]
        if data.get("year"):
            cap_cmd.extend(["--year", str(data['year'])])
        subprocess.run(cap_cmd, check=False)

    log_file = os.path.join(vault_dir, "wiki", "log.md")
    if os.path.exists(log_file):
        log_entry = f"\n\n## [{today}] query | Uitgebreide samenvatting: {data['title']} ({data['author']})\n"
        log_entry += f"Querypagina: [[queries/{today}-{slug}-samenvatting]]\n"
        log_entry += f"Bronpagina: [[sources/{today}-{slug}]]\n"
        log_entry += f"Notities: Automatische samenvatting gegenereerd en verwerkt via Gemini 3.8 Flash. PDF gebouwd, WordPress concept & Showcase gepubliceerd, Capacities Boek-object + Daily Note bijgewerkt.\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_entry)

    status_script = os.path.join(vault_dir, "scripts", "vault-status.py")
    if not os.path.exists(status_script):
        status_script = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/LLM-Workspace/vault-toolkit/scripts/vault-status.py")
    if os.path.exists(status_script):
        subprocess.run([sys.executable, status_script], check=False)

    print("Wachtrij bijwerken (pop)...")
    remaining_lines = lines[1:]
    with open(queue_file, "w", encoding="utf-8") as f:
        f.write("\n".join(remaining_lines) + ("\n" if remaining_lines else ""))

    print(f"=== GESLAAGD! Boek '{data['title']}' is verwerkt. ===")

if __name__ == "__main__":
    main()
