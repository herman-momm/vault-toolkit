#!/usr/bin/env python3
"""
fill-capacities-photos.py — Zoekt foto's op internet voor Capacities-persoonobjecten die nog geen foto hebben,
maakt een MediaImage object aan in Capacities en koppelt deze via het Picture-veld (en een Markdown image block).
"""
import sys, os, re, json, time, argparse, urllib.request, urllib.parse, ssl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultenv

ENV_PATH = vaultenv.config_path("capacities.env")
BASE_URL = "https://api.capacities.io"
PERSON_STRUCTURE_ID = "73abbe4c-adf1-4443-8cb2-20c34b5c029c"
PICTURE_PROP_ID = "69a8ad24-acf6-44fd-acba-7ab2d3a09b62"

ctx = vaultenv.tls_context()


def load_env():
    cfg = vaultenv.load_config("capacities.env", ("CAPACITIES_TOKEN", "CAPACITIES_SPACE_ID"))
    return cfg.get("CAPACITIES_TOKEN"), cfg.get("CAPACITIES_SPACE_ID")


def api_request(endpoint, data=None, method="GET", token=None, retries=5):
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "vault-toolkit/1.0"
    }
    payload = json.dumps(data).encode("utf-8") if data is not None else None
    
    for attempt in range(retries):
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=ctx) as resp:
                res_body = resp.read().decode("utf-8")
                return json.loads(res_body) if res_body else {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait_time = (attempt + 1) * 3
                time.sleep(wait_time)
            else:
                body = e.read().decode("utf-8")
                print(f"  [HTTP {e.code} Error] {endpoint}: {body[:200]}")
                raise e
        except Exception as e:
            if attempt == retries - 1:
                raise e
            time.sleep(2)
    return {}


def fetch_all_personen(token, space_id):
    items = []
    cursor = None
    while True:
        url = f"/objects/structure?spaceId={space_id}&id={PERSON_STRUCTURE_ID}"
        if cursor:
            url += f"&cursor={cursor}"
        res = api_request(url, token=token)
        results = res.get("results", [])
        items.extend(results)
        if res.get("hasMore") and res.get("nextCursor"):
            cursor = res.get("nextCursor")
        else:
            break
        time.sleep(0.5)
    return items


def find_photo_online(name):
    clean_name = re.sub(r'\s*\([^)]*\)', '', name).strip()
    clean_name = re.sub(r'\b(dr\.|prof\.|mr\.)\b', '', clean_name, flags=re.I).strip()

    # 1. Direct Wikipedia Summary (NL, EN)
    for lang in ['nl', 'en']:
        url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(clean_name)}"
        req = urllib.request.Request(url, headers={"User-Agent": "vault-toolkit/1.0"})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
                if data.get("type") in ("standard", "disambiguation"):
                    thumb = data.get("thumbnail", {}).get("source")
                    if thumb:
                        return thumb, f"Wikipedia ({lang.upper()})"
        except Exception:
            pass

    # 2. Wikipedia Search API with title matching
    for lang in ['nl', 'en']:
        search_url = f"https://{lang}.wikipedia.org/w/api.php?action=query&generator=search&gsrsearch={urllib.parse.quote(clean_name)}&gsrlimit=3&prop=pageimages&pithumbsize=600&format=json"
        req = urllib.request.Request(search_url, headers={"User-Agent": "vault-toolkit/1.0"})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
                pages = data.get("query", {}).get("pages", {})
                name_words = [w.lower() for w in clean_name.split() if len(w) > 2]
                for p_id, p_info in pages.items():
                    page_title = p_info.get("title", "").lower()
                    matches = [w for w in name_words if w in page_title]
                    if len(matches) >= max(1, len(name_words) - 1):
                        thumb = p_info.get("thumbnail", {}).get("source")
                        if thumb:
                            return thumb, f"Wikipedia Search ({lang.upper()}: {p_info.get('title')})"
        except Exception:
            pass

    # 3. Wikidata P18 Image
    try:
        wd_url = f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={urllib.parse.quote(clean_name)}&language=en&format=json"
        req = urllib.request.Request(wd_url, headers={"User-Agent": "vault-toolkit/1.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=5) as r:
            wd_data = json.loads(r.read().decode("utf-8"))
            results = wd_data.get("search", [])
            if results:
                q_id = results[0]["id"]
                claims_url = f"https://www.wikidata.org/w/api.php?action=wbgetentities&ids={q_id}&props=claims&format=json"
                req_c = urllib.request.Request(claims_url, headers={"User-Agent": "vault-toolkit/1.0"})
                with urllib.request.urlopen(req_c, context=ctx, timeout=5) as r_c:
                    c_data = json.loads(r_c.read().decode("utf-8"))
                    claims = c_data.get("entities", {}).get(q_id, {}).get("claims", {})
                    if "P18" in claims:
                        img_filename = claims["P18"][0]["mainsnak"]["datavalue"]["value"]
                        img_filename_enc = urllib.parse.quote(img_filename.replace(" ", "_"))
                        commons_url = f"https://commons.wikimedia.org/w/api.php?action=query&titles=File:{img_filename_enc}&prop=imageinfo&iiprop=url&format=json"
                        req_comm = urllib.request.Request(commons_url, headers={"User-Agent": "vault-toolkit/1.0"})
                        with urllib.request.urlopen(req_comm, context=ctx, timeout=5) as r_comm:
                            comm_data = json.loads(r_comm.read().decode("utf-8"))
                            comm_pages = comm_data.get("query", {}).get("pages", {})
                            for cp_id, cp_info in comm_pages.items():
                                ii = cp_info.get("imageinfo", [])
                                if ii and ii[0].get("url"):
                                    return ii[0]["url"], f"Wikidata ({img_filename})"
    except Exception:
        pass

    # 4. Bing Images Search fallback
    try:
        bing_url = f"https://www.bing.com/images/search?q={urllib.parse.quote(clean_name + ' portret')}&form=HDRSC2"
        headers_b = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        }
        req_b = urllib.request.Request(bing_url, headers=headers_b)
        with urllib.request.urlopen(req_b, context=ctx, timeout=8) as r_b:
            html = r_b.read().decode('utf-8', errors='ignore')
            murls = re.findall(r'&quot;murl&quot;:&quot;(http[s]?://[^&"]+)&quot;', html)
            for u in murls:
                u_lower = u.lower()
                if not any(bad in u_lower for bad in ['logo', 'icon', 'banner', 'svg', 'placeholder', 'framed.jpg']):
                    return u, "Bing Images"
    except Exception:
        pass

    return None, None


def attach_photo_to_person(person_id, person_title, photo_url, token, space_id):
    media_url_endpoint = f"/object/url?spaceId={space_id}"
    media_res = api_request(media_url_endpoint, {"url": photo_url}, method="POST", token=token)
    media_id = media_res.get("id")
    
    if not media_id:
        print(f"  [Fout] MediaImage kon niet worden aangemaakt voor {person_title}")
        return False

    patch_endpoint = f"/object?spaceId={space_id}"
    patch_payload = {
        "id": person_id,
        "properties": {
            PICTURE_PROP_ID: {
                "type": "entity",
                "entity": [{"id": media_id}]
            }
        }
    }
    api_request(patch_endpoint, patch_payload, method="PATCH", token=token)

    append_endpoint = f"/blocks/append?spaceId={space_id}"
    md_content = f"![{person_title}]({photo_url})"
    api_request(append_endpoint, {"id": person_id, "markdown": md_content}, method="POST", token=token)

    return True


def main():
    parser = argparse.ArgumentParser(description="Aanvullen van ontbrekende foto's bij Persoon-objecten in Capacities.")
    parser.add_argument("--dry-run", action="store_true", help="Alleen zoeken en tonen wat er zou gebeuren, niks aanpassen")
    parser.add_argument("--limit", type=int, default=0, help="Maximale aantal personen om te verwerken")
    args = parser.parse_args()

    token, space_id = load_env()
    if not token or not space_id:
        sys.exit(f"Geen Capacities token of space-id gevonden (gezocht in {ENV_PATH} "
                 "en in de environment variables CAPACITIES_TOKEN/CAPACITIES_SPACE_ID)")

    print("=== Capacities Persoon-objecten Fotocheck & Aanvulling ===")
    print("Ophalen van alle Persoon-objecten uit Capacities...")
    personen = fetch_all_personen(token, space_id)
    print(f"Totaal aantal Persoon-objecten in Capacities: {len(personen)}")

    missing_photos = []
    has_photos_count = 0

    print("\nControleren welke personen nog geen foto hebben...")
    for i, item in enumerate(personen):
        p_id = item["id"]
        time.sleep(0.5)
        obj_data = api_request(f"/object?spaceId={space_id}&id={p_id}", token=token)
        
        title = obj_data.get("title") or item.get("title") or "Onbekend"
        props = obj_data.get("properties", {})
        pic_prop = props.get(PICTURE_PROP_ID)
        
        has_picture = False
        if pic_prop and (pic_prop.get("targetIds") or pic_prop.get("entity")):
            has_picture = True
            
        blocks = obj_data.get("blocks", {})
        for b_key, b_list in blocks.items():
            for b in b_list:
                if b.get("type") in ("image", "mediaImage") or "![" in str(b):
                    has_picture = True
                    break

        if has_picture:
            has_photos_count += 1
        else:
            missing_photos.append((p_id, title))

        if (i + 1) % 25 == 0 or (i + 1) == len(personen):
            print(f"  Gecontroleerd: {i + 1}/{len(personen)} — {has_photos_count} met foto, {len(missing_photos)} zonder foto")

    print(f"\nResultaat inventarisatie:")
    print(f"  - Met foto: {has_photos_count}")
    print(f"  - ZONDER foto: {len(missing_photos)}")

    if not missing_photos:
        print("\nAlle persoon-objecten in Capacities hebben al een foto! Geen actie nodig.")
        return

    print("\n--- Internet-zoektocht en toevoegen van foto's ---")
    to_process = missing_photos[:args.limit] if args.limit > 0 else missing_photos
    
    success_count = 0
    not_found_list = []

    for idx, (p_id, title) in enumerate(to_process, 1):
        print(f"\n[{idx}/{len(to_process)}] Zoeken naar foto voor: {title}...")
        photo_url, source = find_photo_online(title)
        
        if photo_url:
            print(f"  ✓ Foto gevonden via {source}: {photo_url}")
            if args.dry_run:
                print(f"  [DRY-RUN] Zou MediaImage aanmaken en koppelen aan {title} ({p_id})")
                success_count += 1
            else:
                try:
                    ok = attach_photo_to_person(p_id, title, photo_url, token, space_id)
                    if ok:
                        print(f"  ✓ Succesvol foto gekoppeld in Capacities aan {title}")
                        success_count += 1
                    time.sleep(1.0)
                except Exception as e:
                    print(f"  ✗ Fout bij koppelen foto aan {title}: {e}")
        else:
            print(f"  ✗ Geen geschikte foto online gevonden voor {title}")
            not_found_list.append(title)

    print("\n=== EINDESAMENVATTING ===")
    print(f"Verwerkt: {len(to_process)}")
    print(f"Succesvol voorzien van foto: {success_count}")
    print(f"Niet gevonden: {len(not_found_list)}")
    if not_found_list:
        print("Personen waarvoor online geen foto kon worden gevonden:")
        for name in not_found_list:
            print(f"  - {name}")

if __name__ == "__main__":
    main()

