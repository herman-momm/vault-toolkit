#!/usr/bin/env python3
"""
vault-find.py — Supersnel lokaal zoeken naar pagina's in de actieve vault.

Zoekt op slug, titel en tags in wiki/ en geeft direct bruikbare [[wikilinks]]
terug. Voorkomt dure multi-file regex-scans en bespaart tussen-tokens.

Gebruik (vanuit een vault-root):
  python3 ../vault-toolkit/scripts/vault-find.py "cyber"
  python3 ../vault-toolkit/scripts/vault-find.py "karpathy" --type entity
"""
import sys, os, glob, re, argparse
from collections import defaultdict

ROOT = os.getcwd()
WIKI = os.path.join(ROOT, "wiki")


def parse_frontmatter(path):
    title = os.path.basename(path).removesuffix(".md")
    tags = []
    page_type = "other"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(2048)  # Eerste 2KB is genoeg voor frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                fm = parts[1]
                m_title = re.search(r'^title:\s*["\']?(.*?)["\']?\s*$', fm, re.MULTILINE)
                if m_title: title = m_title.group(1).strip()
                m_type = re.search(r'^type:\s*(\w+)', fm, re.MULTILINE)
                if m_type: page_type = m_type.group(1).strip()
                m_tags = re.search(r'^tags:\s*\[(.*?)\]', fm, re.MULTILINE)
                if m_tags:
                    tags = [t.strip().strip("'\"") for t in m_tags.group(1).split(",") if t.strip()]
    except Exception:
        pass
    return title, page_type, tags


def main():
    parser = argparse.ArgumentParser(description="Zoek razendsnel pagina's in wiki/")
    parser.add_argument("query", help="Zoekterm (titel, slug of tag)")
    parser.add_argument("--type", help="Filter op type (concept, entity, source, query)")
    args = parser.parse_args()

    if not os.path.isdir(WIKI):
        sys.exit(f"Geen wiki/-map gevonden in {ROOT}. Draai vanuit de root van een vault.")

    q = args.query.lower()
    matches = defaultdict(list)

    for md in glob.glob(os.path.join(WIKI, "**", "*.md"), recursive=True):
        rel = os.path.relpath(md, WIKI).removesuffix(".md")
        if rel.startswith("templates/"):
            continue

        title, p_type, tags = parse_frontmatter(md)
        if args.type and p_type != args.type:
            continue

        # Match op slug, titel of tags
        score = 0
        if q in rel.lower(): score += 3
        if q in title.lower(): score += 2
        if any(q in t.lower() for t in tags): score += 1

        if score > 0:
            matches[p_type].append((score, rel, title, tags))

    if not matches:
        print(f"Geen resultaten gevonden voor '{args.query}'.")
        return

    print(f"Zoekresultaten voor '{args.query}' in {os.path.basename(ROOT)}:\n")
    type_order = ["concept", "entity", "source", "query", "overview"]
    all_types = sorted(matches.keys(), key=lambda t: type_order.index(t) if t in type_order else 99)

    for t in all_types:
        items = sorted(matches[t], key=lambda x: (-x[0], x[1]))
        print(f"### {t.capitalize()}s ({len(items)}):")
        for score, rel, title, tags in items[:15]:
            tag_str = f" · tags: {', '.join(tags[:4])}" if tags else ""
            print(f"- [[{rel}|{title}]]{tag_str}")
        if len(items) > 15:
            print(f"- *... en nog {len(items) - 15} andere {t}s*")
        print()


if __name__ == "__main__":
    main()
