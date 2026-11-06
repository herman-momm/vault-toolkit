#!/usr/bin/env python3
"""
gemini-book-runner.py — Geautomatiseerde boeksamenvatting via Gemini 3.8 Flash.
Verwerkt PDF/EPUB boeken uit book-queue.txt in 1 snelle, complete pass.

Vault-hygiëne (toegevoegd 2026-11-05 na de Doggerland-run):
  * links worden na generatie DETERMINISTISCH gesaneerd naar de padconventie
    (`[[entities/slug]]`); wat niet bestaat wordt ontlinkt naar vette tekst,
    zodat een run nooit kapotte links in de vault kan achterlaten;
  * er wordt geen tweede bronpagina aangemaakt voor een raw-bestand dat al
    een bronpagina heeft ("één bronpagina per bron, geen duplicaten");
  * `wiki/index.md` wordt bijgewerkt: catalogusregel + statistiektabel;
  * de run sluit af met `vault-status.py --sync-sources` en meldt luid als er
    toch kapotte links of wezen zijn ontstaan.
"""
import sys
import os
import re
import glob
import json
import ssl
import time
import datetime
import subprocess
import unicodedata
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultenv

try:
    import pymupdf as fitz
except ImportError:
    import fitz

# Zelfde linkgrammatica als vault-status.py: [[doel]], [[doel|label]] en de
# in tabellen gebruikte escape [[doel\|label]].
LINK_RE = re.compile(r"\[\[([^\]|]+?)(?:(\\?)\|([^\]]*))?\]\]")
CODE_RE = re.compile(r"`[^`]*`")
META_PAGES = {"index", "log", "overview", "status"}


def load_env(vault_dir):
    """Zet de sleutels uit claude.env in de omgeving voor DEZE vault.

    Via vaultenv, dus met per-vault override (`~/.config/<vaultnaam>/claude.env`)
    en de gedeelde locatie als terugval. Eerder stond hier het pad van HistVault
    hardgecodeerd, waardoor de runner buiten HistVault geen GEMINI_API_KEY vond
    tenzij die al in de omgeving stond.
    """
    path = vaultenv.config_path("claude.env", explicit=vault_dir)
    for k, v in vaultenv.read_dotenv(path).items():
        if k != "_path":
            os.environ.setdefault(k, v)


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


# --------------------------------------------------------------------------
# Vault-hygiëne
# --------------------------------------------------------------------------

def wiki_pages(vault_dir):
    """Alle wiki-pagina's als {pad-zonder-.md relatief aan wiki/: bestandspad}."""
    wiki = os.path.join(vault_dir, "wiki")
    pages = {}
    for f in glob.glob(os.path.join(wiki, "**", "*.md"), recursive=True):
        rel = os.path.relpath(f, wiki)
        if rel.endswith(".md"):
            rel = rel[:-3]
        pages[rel] = f
    return pages


def _slug(s):
    """Kebab-case-normalisatie, zodat 'Luc Amkreutz' op 'luc-amkreutz' valt."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _page_title(path):
    """De `title:`-waarde uit de frontmatter (lege string als afwezig)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(1200)
    except OSError:
        return ""
    m = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", head, re.M)
    return m.group(1).strip() if m else ""


def build_aliases(pages):
    """Zoektabel slug -> pad, zodat titellinks ([[Doggerland]]) alsnog landen.

    Bestandsnamen gaan voor frontmatter-titels; beide gesorteerd, zodat de
    uitkomst niet van de bestandsvolgorde op schijf afhangt.
    """
    aliases = {}
    for rel in sorted(pages):
        aliases.setdefault(_slug(rel.split("/")[-1]), rel)
    for rel in sorted(pages):
        title = _page_title(pages[rel])
        if title:
            aliases.setdefault(_slug(title), rel)
    return aliases


def resolve_target(target, pages, aliases=None):
    """Resolutie als vault-status.py (pad, dan basename), plus slug-match.

    De slug-stap is de reparatie voor wat de Doggerland-run misging: een model
    schrijft `[[Doggerland]]` waar `entities/doggerland` bedoeld is. Levert de
    slug niets op, dan is het echt geen bestaande pagina en wordt er ontlinkt.
    """
    t = target.strip().rstrip("\\")
    if t.endswith(".md"):
        t = t[:-3]
    if not t:
        return None
    if t in pages:
        return t
    hits = sorted(p for p in pages if p.split("/")[-1] == t)
    if hits:
        return hits[0]
    if aliases is None:
        aliases = build_aliases(pages)
    return aliases.get(_slug(t))


def sanitize_wikilinks(text, pages, aliases=None):
    """Maak elke [[link]] geldig óf geen link meer.

    Een taalmodel schrijft graag Obsidian-titellinks (`[[Doggerland]]`) en
    verzint "Zie ook"-pagina's die niet bestaan. Deze functie zet alles wat
    oplosbaar is om naar de padvorm en ontlinkt de rest naar vette tekst.
    Retourneert (nieuwe_tekst, ontlinkte_doelen).
    """
    if aliases is None:
        aliases = build_aliases(pages)
    stripped = []

    def repl(m):
        raw_target = m.group(1).strip()
        escape, label = m.group(2) or "", m.group(3)
        label = label.strip() if label else ""
        # Ankers en raw/-verwijzingen laten we ongemoeid: die resolveert
        # vault-status.py tegen raw/ respectievelijk de pagina zelf.
        if raw_target.startswith("#") or raw_target.startswith("raw/"):
            return m.group(0)
        # exports/ en URL's zijn geen wiki-pagina's en horen nooit in [[ ]].
        if raw_target.startswith("exports/") or raw_target.startswith("http"):
            stripped.append(raw_target)
            return f"**{label or os.path.basename(raw_target)}**"
        hit = resolve_target(raw_target, pages, aliases)
        if not hit:
            stripped.append(raw_target)
            return f"**{label or raw_target}**"
        if label and label != hit:
            return f"[[{hit}{escape}|{label}]]"
        return f"[[{hit}]]"

    return LINK_RE.sub(repl, text), stripped


def existing_source_page(vault_dir, book_filename):
    """Bestaat er al een bronpagina voor dit raw-bestand? (voorkomt duplicaten)

    Zelfde criterium als de ingest-achterstandscheck van vault-status.py: de
    bestandsnaam komt ergens in de bronpagina voor (meestal in `raw:`).
    """
    for f in sorted(glob.glob(os.path.join(vault_dir, "wiki", "sources", "*.md"))):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                if book_filename in fh.read():
                    rel = os.path.relpath(f, os.path.join(vault_dir, "wiki"))
                    return rel[:-3] if rel.endswith(".md") else rel
        except OSError:
            continue
    return None


def frontmatter_sources(vault_dir, rel_page):
    """De `sources:`-waarde uit de frontmatter van een pagina (0 als afwezig)."""
    path = os.path.join(vault_dir, "wiki", rel_page + ".md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            m = re.search(r"^sources:\s*(\d+)", f.read(), re.M)
            return int(m.group(1)) if m else 0
    except OSError:
        return 0


def _find_section(lines, needles):
    """Geef (start, eind) van de bullets onder de eerste '## '-kop die matcht."""
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("## "):
            if start is not None:
                return start, i
            if any(n.lower() in ln.lower() for n in needles):
                start = i + 1
    if start is not None:
        return start, len(lines)
    return None, None


def add_index_line(lines, needles, new_line):
    """Voeg een catalogusregel toe aan het eind van een indexsectie.

    De regel komt onder een eigen subkop, zodat een automatische run nooit een
    boek onder een inhoudelijk verkeerde subkop parkeert; een latere lint kan
    hem verplaatsen.
    """
    marker = "**Automatisch toegevoegd (nog niet geordend)**"
    start, end = _find_section(lines, needles)
    if start is None:
        return False
    if any(new_line.strip() == ln.strip() for ln in lines[start:end]):
        return True
    block = lines[start:end]
    if marker in block:
        pos = start + block.index(marker) + 1
        while pos < end and lines[pos].strip().startswith("- "):
            pos += 1
        lines.insert(pos, new_line)
        return True
    tail = end
    while tail > start and not lines[tail - 1].strip():
        tail -= 1
    lines[tail:tail] = ["", marker, new_line]
    return True


def update_index(vault_dir, today, entries):
    """Werk index.md bij: catalogusregels + statistiektabel + datum.

    `entries` is een lijst van (soort, regel) met soort in {"query", "source"}.
    """
    path = os.path.join(vault_dir, "wiki", "index.md")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    added = []
    for kind, line in entries:
        needles = ["wiki/queries", "Vragen", "Queries"] if kind == "query" \
            else ["wiki/sources", "Bronnen"]
        if add_index_line(lines, needles, line):
            added.append(kind)

    wiki = os.path.join(vault_dir, "wiki")
    pages = wiki_pages(vault_dir)
    counts = {
        "Bronnen verwerkt": len(glob.glob(os.path.join(wiki, "sources", "*.md"))),
        "Entiteiten": len(glob.glob(os.path.join(wiki, "entities", "*.md"))),
        "Concepten": len(glob.glob(os.path.join(wiki, "concepts", "*.md"))),
        "Opgeslagen queries": len(glob.glob(os.path.join(wiki, "queries", "*.md"))),
        "Wiki-pagina's": len([p for p in pages
                              if not p.startswith("templates/") and p not in META_PAGES]),
    }
    out = []
    for ln in lines:
        m = re.match(r"\|\s*(Bronnen verwerkt|Wiki-pagina's|Entiteiten|Concepten|Opgeslagen queries)\s*\|\s*\d+\s*\|", ln)
        if m:
            out.append(f"| {m.group(1)} | {counts[m.group(1)]} |")
            continue
        if ln.startswith("*Laatste update:"):
            out.append(f"*Laatste update: {today}*")
            continue
        if re.match(r"^updated:\s*\d{4}-\d{2}-\d{2}", ln):
            out.append(f"updated: {today}")
            continue
        out.append(ln)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return added


def run_status(vault_dir):
    """Draai vault-status.py --sync-sources en geef de samenvattingsregel terug."""
    script = os.path.join(vault_dir, "scripts", "vault-status.py")
    if not os.path.exists(script):
        script = os.path.expanduser(
            "~/Library/Mobile Documents/com~apple~CloudDocs/LLM-Workspace/vault-toolkit/scripts/vault-status.py")
    if not os.path.exists(script):
        return ""
    res = subprocess.run([sys.executable, script, "--sync-sources"],
                         capture_output=True, text=True)
    out = (res.stdout or "").strip().splitlines()
    return out[-1] if out else ""


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

    # Eén context, mét certificaatcontrole. Hier stond een try/except die bij
    # élke uitzondering — ook een time-out of DNS-fout — terugviel op een
    # ongeverifieerde context; de API-sleutel ging dan alsnog de deur uit, maar
    # dan zonder dat het certificaat was gecontroleerd. Faalt dit op het
    # certificaat, dan is dat een signaal en geen reden om door te gaan.
    response_obj = urllib.request.urlopen(req, timeout=180,
                                          context=vaultenv.tls_context())

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

    # Bestaat er al een bronpagina voor dit raw-bestand? Dan niet dupliceren.
    pages = wiki_pages(vault_dir)
    existing_src = existing_source_page(vault_dir, book_filename)
    if existing_src:
        print(f"Bronpagina bestaat al: {existing_src} — er wordt geen tweede aangemaakt.")

    # Inventaris van bestaande linkdoelen; het model mag alleen hiernaar linken.
    linkable = sorted(p for p in pages
                      if p.startswith(("entities/", "concepts/", "sources/", "queries/")))
    inventory = "\n".join(linkable)

    source_instruction = (
        f'"source_markdown": "LAAT DIT EEN LEGE STRING. De bronpagina bestaat al '
        f'({existing_src}) en mag NIET opnieuw worden aangemaakt."'
        if existing_src else
        f'"source_markdown": "Volledige inhoud voor wiki/sources/{today}-<slug>.md '
        f'(bronpagina frontmatter type: source, title, author, year, publisher, isbn, '
        f'created: {today}, updated: {today}, sources: 0, raw: \\"raw/{book_filename}\\", '
        f'met korte samenvatting en ## Zie ook met link naar de querypagina)"'
    )
    source_link_hint = existing_src if existing_src else f"sources/{today}-<slug>"

    system_prompt = f"""
Je bent een senior academische analist en vault-beheerder voor een Obsidian/Karpathy-style LLM Wiki kennisbank.
Geef een uitputtende, hoogwaardige analyse en samenvatting van het onderstaande boek.

VANDAAG IS: {today}
BESTANDSNAAM IN RAW: raw/{book_filename}

=== LINKREGELS (STRIKT — hier is eerder de vault door bedorven) ===
1. Interne links hebben ALTIJD de padvorm met map: `[[entities/luc-amkreutz|Luc Amkreutz]]`,
   `[[concepts/mesolithicum]]`, `[[sources/2021-doggerland-verdwenen-wereld]]`.
   NOOIT de Obsidian-titelvorm `[[Doggerland]]` of `[[Luc Amkreutz]]`.
2. Je mag UITSLUITEND linken naar paden die letterlijk in de INVENTARIS hieronder staan
   (plus de bron-/querypagina van dit boek zelf). Bestaat een pagina niet, dan schrijf je
   de naam als **vette tekst** — geen link. Verzin NOOIT een pagina.
3. In de sectie `## Zie ook` staan alleen echte pagina's. Zet er geen bedachte
   paginanamen in die eigenlijk sectiekoppen zijn (zoals "Mesolithische Wetlands en Subsistentie").
4. Nooit `[[exports/...]]`, nooit een URL in `[[ ]]`: exports en websites zijn geen wiki-pagina's.
5. In tabelcellen moet de pipe van een link geëscaped zijn: `[[entities/krijn\\|Krijn]]`.
6. De bronpagina van dit boek is: `{source_link_hint}` — link daarnaar vanuit de querypagina.

=== INVENTARIS VAN BESTAANDE PAGINA'S (de enige toegestane linkdoelen) ===
{inventory}

GEEF JE ANTWOORD IN EXACT DE VOLGENDE JSON STRUCTUUR (zonder markdown codeblocks buiten de JSON, antwoord alleen valide JSON):
{{
  "slug": "kort-kebab-case-slug",
  "title": "Exacte Hoofdtitel van het Boek",
  "author": "Auteur(s)",
  "year": "Jaar van uitgave (YYYY)",
  "publisher": "Uitgeverij",
  "isbn": "ISBN indien gevonden anders leeg",
  "category": "Geschiedenis / Archeologie / etc.",
  "index_description": "EEN regel van max 40 woorden voor de catalogusregel in index.md: wat deze samenvatting behandelt, zonder titel/auteur te herhalen",
  "query_markdown": "Volledige inhoud voor wiki/queries/{today}-<slug>-samenvatting.md (Obsidian Markdown; frontmatter met type: query, title, created: {today}, updated: {today}, sources: 0, tags; links volgens de LINKREGELS; overzichtstabellen, blockquotes, en een ## Zie ook sectie)",
  "export_markdown": "Volledige inhoud voor exports/<slug>-samenvatting.md (verhalende, uitgebreide analyse 1800-2800 woorden, kernthese, hoofdstuk-voor-hoofdstuk opbouw, meerdere tabellen, kernquotes, betekenis. Geen emoji, geen wikilinks in export, PDF-geschikt)",
  {source_instruction}
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

    query_rel = f"queries/{today}-{slug}-samenvatting"
    source_rel = existing_src or f"sources/{today}-{slug}"
    query_file = os.path.join(vault_dir, "wiki", query_rel + ".md")
    export_file = os.path.join(vault_dir, "exports", f"{slug}-samenvatting.md")
    source_file = os.path.join(vault_dir, "wiki", source_rel + ".md")

    # De pagina's die deze run zelf aanmaakt gelden ook als geldig linkdoel.
    known = dict(pages)
    known[query_rel] = query_file
    known[source_rel] = source_file
    aliases = build_aliases(known)

    query_md, stripped_q = sanitize_wikilinks(data["query_markdown"].strip(), known, aliases)
    print(f"Schrijven naar querypagina: {query_file}")
    with open(query_file, "w", encoding="utf-8") as f:
        f.write(query_md + "\n")

    print(f"Schrijven naar exportbestand: {export_file}")
    with open(export_file, "w", encoding="utf-8") as f:
        f.write(data["export_markdown"].strip() + "\n")

    stripped_s = []
    source_written = False
    source_md = (data.get("source_markdown") or "").strip()
    if existing_src:
        print(f"Bronpagina overgeslagen (bestaat al): {existing_src}")
    elif os.path.exists(source_file):
        print(f"Bronpagina bestaat al op dit pad, niet overschreven: {source_file}")
    elif source_md:
        source_md, stripped_s = sanitize_wikilinks(source_md, known, aliases)
        print(f"Schrijven naar bronpagina: {source_file}")
        with open(source_file, "w", encoding="utf-8") as f:
            f.write(source_md + "\n")
        source_written = True

    ontlinkt = sorted(set(stripped_q + stripped_s))
    if ontlinkt:
        print(f"Sanitizer: {len(ontlinkt)} niet-bestaande linkdoelen ontlinkt naar vette tekst.")
        for t in ontlinkt:
            print(f"  - {t}")

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
        cap_cmd = [
            sys.executable, cap_script,
            "--book-md", export_file,
            "--title", data['title'],
            "--author", data['author'],
            "--desc", f"Boek van {data['author']}"
        ]
        if data.get("year"):
            cap_cmd.extend(["--year", str(data['year'])])
        subprocess.run(cap_cmd, check=False)

    print("index.md bijwerken (catalogusregel + statistiek)...")
    desc = (data.get("index_description") or "").strip().rstrip(".")
    entries = [("query",
                f"- [[{query_rel}|Uitgebreide samenvatting: {data['title']} ({data['author']})]]"
                f"{' — ' + desc if desc else ''} (bronnen: {frontmatter_sources(vault_dir, query_rel)})")]
    if source_written:
        entries.append(("source",
                        f"- [[{source_rel}|{data['title']} ({data['author']}, {data.get('year', '')})]]"
                        f"{' — ' + desc if desc else ''} (bronnen: {frontmatter_sources(vault_dir, source_rel)})"))
    added = update_index(vault_dir, today, entries)
    if len(added) < len(entries):
        print("LET OP: niet elke catalogusregel kon worden geplaatst — controleer index.md.", file=sys.stderr)

    status_line = run_status(vault_dir)
    if status_line:
        print(f"vault-status: {status_line}")
    kapot = re.search(r"(\d+)\s+kapotte links", status_line)
    wezen = re.search(r"(\d+)\s+wezen", status_line)
    lint_warn = ""
    if (kapot and int(kapot.group(1))) or (wezen and int(wezen.group(1))):
        lint_warn = f" LET OP — lint na de run: {status_line}."
        print(f"\n!!! LINT-WAARSCHUWING: {status_line}\n", file=sys.stderr)

    log_file = os.path.join(vault_dir, "wiki", "log.md")
    if os.path.exists(log_file):
        log_entry = f"\n\n## [{today}] query | Uitgebreide samenvatting: {data['title']} ({data['author']})\n"
        log_entry += f"Querypagina: [[{query_rel}]]\n"
        log_entry += f"Bronpagina: [[{source_rel}]]{' (bestond al; niet gedupliceerd)' if existing_src else ''}\n"
        log_entry += ("Notities: Automatische samenvatting gegenereerd en verwerkt via Gemini 3.8 Flash. "
                      "PDF gebouwd, WordPress concept & Showcase gepubliceerd, Capacities Boek-object + Daily Note bijgewerkt. "
                      "index.md bijgewerkt (catalogusregel onder 'Automatisch toegevoegd' + statistiek).")
        if ontlinkt:
            log_entry += (f" Sanitizer ontlinkte {len(ontlinkt)} niet-bestaande linkdoelen naar vette tekst "
                          f"(kandidaten voor een eigen pagina: {', '.join(ontlinkt[:15])}"
                          f"{' …' if len(ontlinkt) > 15 else ''}).")
        if status_line:
            log_entry += f" Eindstand vault-status: {status_line}."
        log_entry += lint_warn + "\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_entry)

    print("Wachtrij bijwerken (pop)...")
    remaining_lines = lines[1:]
    with open(queue_file, "w", encoding="utf-8") as f:
        f.write("\n".join(remaining_lines) + ("\n" if remaining_lines else ""))

    print(f"=== GESLAAGD! Boek '{data['title']}' is verwerkt. ===")


if __name__ == "__main__":
    main()
