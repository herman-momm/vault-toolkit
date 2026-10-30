#!/usr/bin/env python3
"""Upload een cover-afbeelding naar de WP-mediabibliotheek en print de publieke URL.

Gebruik: python3 scripts/wp-upload-cover.py <coverbestand>
Print bij succes alleen de source_url (bruikbaar als publieke URL, o.a. voor Capacities uploadFile).
"""
import os, sys, re, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("wpdraft", os.path.join(HERE, "wp-publish-draft.py"))
_wp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_wp)


def main():
    if len(sys.argv) < 2:
        sys.exit("gebruik: wp-upload-cover.py <coverbestand>")
    path = sys.argv[1]
    if not os.path.exists(path):
        sys.exit("bestaat niet: " + path)
    env = _wp.load_env()
    url = env["WP_URL"].rstrip("/")
    user = env["WP_USER"]
    pw = env["WP_APP_PASSWORD"].replace(" ", "")
    slug = re.sub(r"[^A-Za-z0-9.-]+", "-", os.path.basename(path))
    mid, murl = _wp.upload_media(path, url, user, pw, slug)
    if not murl:
        sys.exit(1)
    print(murl)


if __name__ == "__main__":
    main()
