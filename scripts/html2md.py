#!/usr/bin/env python3
"""
html2md.py — Converteer ruwe web-HTML naar compacte Markdown.
Bespaart 80% tot 95% tokens door alle HTML-ballast lokaal te strippen.
"""
import sys, os, re, html, argparse
from html.parser import HTMLParser


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


def clean_html_content(raw_html: str) -> str:
    source_url = ""
    m = re.search(r"<!--\s*source-url:\s*(https?://[^\s>]+)\s*-->", raw_html)
    if m: source_url = m.group(1).strip()

    parser = SimpleHTMLToMarkdown()
    parser.feed(raw_html)
    text = "".join(parser.output)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    header = f"<!-- source-url: {source_url} -->\n\n" if source_url else ""
    return header + text + "\n"


def convert_file(path: str, replace: bool = False) -> str:
    if not os.path.exists(path):
        print(f"Bestand niet gevonden: {path}", file=sys.stderr)
        return ""

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()

    cleaned = clean_html_content(raw)
    out_path = os.path.splitext(path)[0] + ".md"

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(cleaned)

    orig_size = len(raw.encode("utf-8"))
    clean_size = len(cleaned.encode("utf-8"))
    saved_pct = round((1 - clean_size / orig_size) * 100, 1) if orig_size > 0 else 0

    print(f"Geconverteerd: {os.path.basename(path)} -> {os.path.basename(out_path)}")
    print(f"  Grootte: {orig_size:,} bytes -> {clean_size:,} bytes ({saved_pct}% besparing)")

    if replace and out_path != path:
        os.remove(path)
        print(f"  Originele .html verwijderd.")

    return out_path


def main():
    parser = argparse.ArgumentParser(description="Strip ruwe HTML naar schone Markdown")
    parser.add_argument("file", nargs="?", help="Pad naar .html-bestand")
    parser.add_argument("--dir", help="Map met .html-bestanden")
    parser.add_argument("--replace", action="store_true", help="Verwijder .html na conversie")
    args = parser.parse_args()

    if args.dir:
        if not os.path.isdir(args.dir):
            sys.exit(f"Map niet gevonden: {args.dir}")
        for f in sorted(os.listdir(args.dir)):
            if f.endswith((".html", ".htm")):
                convert_file(os.path.join(args.dir, f), replace=args.replace)
    elif args.file:
        convert_file(args.file, replace=args.replace)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
