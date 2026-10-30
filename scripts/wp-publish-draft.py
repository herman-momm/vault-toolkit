#!/usr/bin/env python3
"""Publiceer een export-markdown als CONCEPT (draft) op WordPress.

Gebruik:
  python3 scripts/wp-publish-draft.py <export.md> [--title "Titel"] [--status draft]

Leest credentials uit (in volgorde): $VAULT_WP_ENV, anders
~/.config/<vaultnaam>/wp.env (vaultnaam = lowercase basename van de cwd —
draai dit script dus altijd vanuit de vault-root), anders env vars
(WP_URL, WP_USER, WP_APP_PASSWORD, WP_GS_BOOKS_LICENSE) — dat laatste is het
normale pad in een cloud-sessie zonder lokaal credentialbestand.

Zet ALTIJD status=draft tenzij expliciet anders; publiceert dus niet openbaar.
Print bij succes de post-id en de bewerk-URL (wp-admin).
"""
import sys, os, re, json, argparse, tempfile, subprocess, html as _html

ENV = os.environ.get("VAULT_WP_ENV") or os.path.expanduser(
    f"~/.config/{os.path.basename(os.getcwd()).lower()}/wp.env"
)
VAULT_NAME = os.path.basename(os.getcwd()) or "vault"


def load_env():
    d = {}
    if os.path.exists(ENV):
        with open(ENV) as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                d[k.strip()] = v
    # Val terug op environment variables (cloud-omgevingen zonder lokaal wp.env-bestand).
    for key in ("WP_URL", "WP_USER", "WP_APP_PASSWORD", "WP_GS_BOOKS_LICENSE"):
        if key not in d and key in os.environ:
            d[key] = os.environ[key]
    return d


def esc(s):
    return _html.escape(s, quote=False)


def inline(s):
    s = esc(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def md_to_html(md):
    lines = md.split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        # lege regel
        if not stripped:
            i += 1
            continue
        # tabel: header + scheidingsregel
        if stripped.startswith("|") and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]) and "-" in lines[i + 1]:
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            t = ["<table><thead><tr>"] + [f"<th>{inline(c)}</th>" for c in header] + ["</tr></thead><tbody>"]
            for r in rows:
                t.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            continue
        # horizontale lijn
        if re.match(r"^-{3,}$", stripped):
            out.append("<hr/>")
            i += 1
            continue
        # kop
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        # blockquote
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote><p>" + inline(" ".join(buf)) + "</p></blockquote>")
            continue
        # opsomming
        if re.match(r"^[-*]\s+", stripped):
            buf = []
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                buf.append(re.sub(r"^[-*]\s+", "", lines[i].strip()))
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in buf) + "</ul>")
            continue
        # genummerde lijst
        if re.match(r"^\d+\.\s+", stripped):
            buf = []
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                buf.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            out.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in buf) + "</ol>")
            continue
        # alinea (meerdere regels tot lege regel)
        buf = []
        while i < n and lines[i].strip() and not re.match(r"^(#{1,6}\s|[-*]\s|\d+\.\s|>|\|)", lines[i].strip()) and not re.match(r"^-{3,}$", lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>" + inline(" ".join(buf)) + "</p>")
    return "\n".join(out)


def _wrap_items(inner):
    return re.sub(r"<li>(.*?)</li>",
                  lambda m: f"<!-- wp:list-item --><li>{m.group(1)}</li><!-- /wp:list-item -->",
                  inner, flags=re.S)


def html_to_blocks(html):
    """Zet de vlakke HTML uit md_to_html om naar Gutenberg-blokmarkup."""
    if "<!-- wp:" in html:
        return html  # al blokken
    out = []
    for el in [l.strip() for l in html.split("\n") if l.strip()]:
        m = re.match(r"<h([2-6])>(.*)</h\1>$", el, re.S)
        if m:
            lvl = m.group(1)
            out.append(f'<!-- wp:heading {{"level":{lvl}}} -->\n'
                       f'<h{lvl} class="wp-block-heading">{m.group(2)}</h{lvl}>\n<!-- /wp:heading -->')
        elif el.startswith("<p>") and el.endswith("</p>"):
            out.append(f"<!-- wp:paragraph -->\n{el}\n<!-- /wp:paragraph -->")
        elif el.startswith("<ul>") and el.endswith("</ul>"):
            out.append('<!-- wp:list -->\n<ul class="wp-block-list">'
                       + _wrap_items(el[4:-5]) + "</ul>\n<!-- /wp:list -->")
        elif el.startswith("<ol>") and el.endswith("</ol>"):
            out.append('<!-- wp:list {"ordered":true} -->\n<ol class="wp-block-list">'
                       + _wrap_items(el[4:-5]) + "</ol>\n<!-- /wp:list -->")
        elif el.startswith("<table>") and el.endswith("</table>"):
            out.append(f'<!-- wp:table -->\n<figure class="wp-block-table">{el}</figure>\n<!-- /wp:table -->')
        elif el.startswith("<blockquote>"):
            inner = re.sub(r"^<blockquote>(.*)</blockquote>$", r"\1", el, flags=re.S)
            inner = re.sub(r"<p>(.*?)</p>",
                           lambda m: f"<!-- wp:paragraph --><p>{m.group(1)}</p><!-- /wp:paragraph -->",
                           inner, flags=re.S)
            out.append(f'<!-- wp:quote -->\n<blockquote class="wp-block-quote">{inner}</blockquote>\n<!-- /wp:quote -->')
        elif re.match(r"<hr\s*/?>$", el):
            out.append('<!-- wp:separator -->\n<hr class="wp-block-separator has-alpha-channel-opacity"/>\n<!-- /wp:separator -->')
        elif el.startswith("<figure") and "wp-block" not in el:
            inner = re.sub(r"^<figure[^>]*>(.*)</figure>$", r"\1", el, flags=re.S)
            out.append(f'<!-- wp:image {{"align":"center"}} -->\n'
                       f'<figure class="wp-block-image aligncenter">{inner}</figure>\n<!-- /wp:image -->')
        else:
            out.append(f"<!-- wp:html -->\n{el}\n<!-- /wp:html -->")
    return "\n\n".join(out)


def image_size(path):
    """Breedte/hoogte van een JPEG/PNG zonder externe libs; (w, h) of (None, None)."""
    import struct
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return w, h
            f.seek(2)
            while True:
                b = f.read(1)
                while b and b != b"\xff":
                    b = f.read(1)
                marker = f.read(1)
                while marker == b"\xff":
                    marker = f.read(1)
                if marker in (b"\xc0", b"\xc1", b"\xc2", b"\xc3"):
                    f.read(3)
                    h, w = struct.unpack(">HH", f.read(4))
                    return w, h
                size = struct.unpack(">H", f.read(2))[0]
                f.seek(size - 2, 1)
    except Exception:
        return None, None


def cover_image_block(murl, alt, mid=None, natural_w=None, natural_h=None, disp_w=240):
    """Geldig, gecentreerd, verkleind Gutenberg-image-blok voor de omslag."""
    dh = round(disp_w * natural_h / natural_w) if (natural_w and natural_h) else round(disp_w * 1.45)
    idpart = f'"id":{mid},' if mid else ""
    clspart = f' class="wp-image-{mid}"' if mid else ""
    return (f'<!-- wp:image {{{idpart}"width":{disp_w},"height":{dh},"sizeSlug":"large","align":"center"}} -->\n'
            f'<figure class="wp-block-image aligncenter size-large is-resized">'
            f'<img src="{murl}" alt="{alt}"{clspart} width="{disp_w}" height="{dh}"/></figure>\n'
            f'<!-- /wp:image -->')


def upload_media(path, url, user, pw, filename=None):
    """Upload een afbeelding naar de WP-mediabibliotheek. Geeft (id, source_url)."""
    filename = filename or os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()
    ctype = "image/png" if ext == ".png" else "image/jpeg"
    out = path + ".mediaout"
    cfg = (
        f'url = "{url}/wp-json/wp/v2/media"\n'
        f'user = "{user}:{pw}"\n'
        'request = "POST"\n'
    )
    try:
        proc = subprocess.run(
            ["curl", "-sS", "-m", "90", "-K", "-",
             "-H", f"Content-Type: {ctype}",
             "-H", f'Content-Disposition: attachment; filename="{filename}"',
             "--data-binary", "@" + path,
             "-o", out, "-w", "%{http_code}"],
            input=cfg, text=True, capture_output=True,
        )
        code = proc.stdout.strip()
        raw = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
    finally:
        try:
            os.remove(out)
        except OSError:
            pass
    if code not in ("200", "201"):
        sys.stderr.write(f"COVER-UPLOAD FOUT http={code}: {raw[:300]}\n")
        return None, None
    d = json.loads(raw)
    return d.get("id"), d.get("source_url")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mdfile")
    ap.add_argument("--title", default=None)
    ap.add_argument("--status", default="draft")
    ap.add_argument("--excerpt", default=None)
    ap.add_argument("--type", default="posts",
                    help="REST rest_base van het posttype (bv. 'posts' of 'knowledgebase')")
    ap.add_argument("--cover-file", dest="cover_file", default="",
                    help="pad naar een cover-afbeelding; wordt geupload en als uitgelichte afbeelding gezet")
    args = ap.parse_args()

    md = open(args.mdfile, encoding="utf-8").read()

    title = args.title
    if not title:
        m = re.search(r"^#\s+(.*)$", md, re.M)
        title = m.group(1).strip() if m else os.path.basename(args.mdfile)
    # verwijder de eerste H1 uit de body (wordt de posttitel)
    body_md = re.sub(r"^#\s+.*$", "", md, count=1, flags=re.M).strip()
    content = md_to_html(body_md)

    env = load_env()
    url = env["WP_URL"].rstrip("/")
    user = env["WP_USER"]
    pw = env["WP_APP_PASSWORD"].replace(" ", "")

    # Body eerst naar blokken; daarna de cover als correct image-blok vooraan
    content = html_to_blocks(content)
    cover_mid = None
    if args.cover_file and os.path.exists(args.cover_file):
        cslug = re.sub(r"[^A-Za-z0-9.-]+", "-", os.path.basename(args.cover_file))
        cover_mid, cover_murl = upload_media(args.cover_file, url, user, pw, cslug)
        if cover_murl:
            alt = _html.escape("Omslag " + title.replace(" — uitgebreide samenvatting", ""), quote=True)
            w, h = image_size(args.cover_file)
            content = cover_image_block(cover_murl, alt, cover_mid, w, h) + "\n\n" + content

    payload = {"title": title, "content": content, "status": args.status}
    if args.excerpt:
        payload["excerpt"] = args.excerpt

    # Payload naar tijdelijk bestand; POST via curl (systeemcertificaten).
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as pf:
        json.dump(payload, pf, ensure_ascii=False)
        payload_path = pf.name
    body_path = payload_path + ".out"
    # curl-config via stdin (-K -) zodat het wachtwoord niet in de proceslijst staat.
    curl_cfg = (
        f'url = "{url}/wp-json/wp/v2/{args.type}"\n'
        f'user = "{user}:{pw}"\n'
        'request = "POST"\n'
        'header = "Content-Type: application/json"\n'
        f'user-agent = "{VAULT_NAME}-daily"\n'
        f'data-binary = "@{payload_path}"\n'
    )
    try:
        proc = subprocess.run(
            ["curl", "-sS", "-m", "45", "-K", "-", "-o", body_path,
             "-w", "%{http_code}"],
            input=curl_cfg, text=True, capture_output=True,
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
        sys.stderr.write(f"FOUT http={code}: {raw[:500]}\n{proc.stderr[:300]}\n")
        sys.exit(1)
    res = json.loads(raw)

    pid = res.get("id")
    print(f"OK: concept aangemaakt (id={pid}, status={res.get('status')})")
    print(f"Titel: {title}")
    print(f"Bewerken: {url}/wp-admin/post.php?post={pid}&action=edit")

    # Reeds geuploade cover (cover_mid) als uitgelichte afbeelding zetten
    if cover_mid and pid:
        fcfg = (
            f'url = "{url}/wp-json/wp/v2/{args.type}/{pid}"\n'
            f'user = "{user}:{pw}"\nrequest = "POST"\n'
            'header = "Content-Type: application/json"\n'
            f'data-binary = "{{\\"featured_media\\": {cover_mid}}}"\n'
        )
        fp = subprocess.run(["curl", "-sS", "-m", "30", "-K", "-", "-o", "/dev/null",
                             "-w", "%{http_code}"], input=fcfg, text=True, capture_output=True)
        print(f"Cover: media {cover_mid} — body + featured image (featured http={fp.stdout.strip()})")


if __name__ == "__main__":
    main()
