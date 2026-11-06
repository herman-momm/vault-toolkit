#!/usr/bin/env python3
"""vaultenv.py — gedeelde vault- en credentialresolutie voor de toolkit-scripts.

Waarom dit bestaat: de scripts hardcodeerden alle vier (capacities, wp, gemini,
blob) het pad `~/.config/histvault/<naam>.env`, óók als ze vanuit ClaudeVault of
GroeneVault draaiden. Dat werkte, maar was misleidend — die map is in de praktijk
de *gedeelde* configmap, niet die van HistVault — en het maakte een per-vault
afwijking onmogelijk zonder het script te forken. Hier staat die logica één keer.

Gebruik in een toolkit-script:

    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import vaultenv

    cfg = vaultenv.load_config("capacities.env", ("CAPACITIES_TOKEN", "CAPACITIES_SPACE_ID"))
    root = vaultenv.vault_root()

Zoekorde voor een configbestand `<naam>.env`:
  1. $VAULT_<NAAM>_ENV            (bv. $VAULT_WP_ENV voor wp.env)
  2. ~/.config/<vaultnaam>/<naam> (per-vault override, bv. ~/.config/groenevault/wp.env)
  3. ~/.config/vaults/<naam>      (expliciet gedeeld voor alle vaults)
  4. ~/.config/histvault/<naam>   (historisch gedeelde locatie — waar alles nu staat)
Losse environment variables gaan vóór een bestand als ze gezet zijn, zodat een
cloud-sessie zonder credentialbestand gewoon werkt.

Daarnaast staat hier `tls_context()`: één plek die een SSL-context mét
certificaatcontrole oplevert. Zie de docstring daar voor het waarom.
"""
import os
import ssl
import sys

# Suffix per vault voor env-varianten die naast elkaar moeten kunnen bestaan
# (de Vercel Blob-tokens): ClaudeVault gebruikt de generieke naam, de andere
# twee een gesuffixte, zodat drie vaults in één omgeving niet botsen.
VAULT_ENV_SUFFIX = {
    "claudevault": "",
    "histvault": "HIST",
    "groenevault": "GREEN",
}


def ca_bundle():
    """Pad naar een bruikbare CA-bundel, plus waar die vandaan komt.

    Retourneert (pad_of_None, omschrijving). None betekent: de standaardpaden
    van deze Python volstaan.
    """
    override = os.environ.get("SSL_CERT_FILE")
    if override and os.path.isfile(override):
        return override, f"$SSL_CERT_FILE ({override})"

    if ssl.get_default_verify_paths().cafile:
        return None, "standaardpaden van deze Python"

    try:
        import certifi
        pad = certifi.where()
        if os.path.isfile(pad):
            return pad, f"certifi ({pad})"
    except ImportError:
        pass

    for pad in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem",
                "/usr/local/etc/openssl/cert.pem",
                "/opt/homebrew/etc/openssl@3/cert.pem"):
        if os.path.isfile(pad):
            return pad, f"systeembundel ({pad})"

    return None, "geen bundel gevonden"


def tls_context():
    """Een SSL-context MET certificaatcontrole.

    Waarom dit bestaat: de toolkit-scripts gebruikten allemaal
    `ssl._create_unverified_context()`, of probeerden verificatie en vielen bij
    élke uitzondering stil terug op een ongeverifieerde context. Daardoor ging
    bij elke API-aanroep een bearer-token de deur uit zonder dat het certificaat
    van de tegenpartij werd gecontroleerd.

    De oorzaak was niet de API maar deze Mac: de python.org-build wijst
    `openssl_cafile` naar een bestand dat niet bestaat, omdat
    "Install Certificates.command" nooit is gedraaid. Elke https-aanroep faalde
    dus met CERTIFICATE_VERIFY_FAILED, en uitzetten leek de uitweg. Een bundel is
    er wél — via `certifi` of de systeembundel van macOS — dus die zoeken we op.

    Faalt een aanroep hierna alsnog op het certificaat, dan is dat een echt
    signaal: niet wegklikken met een ongeverifieerde context, maar uitzoeken.
    Zet desnoods `SSL_CERT_FILE` naar een bundel die je vertrouwt.
    """
    pad, _ = ca_bundle()
    return ssl.create_default_context(cafile=pad) if pad \
        else ssl.create_default_context()


def vault_root(explicit=None, marker="wiki"):
    """De root van de vault waarop we werken.

    Volgorde: expliciet meegegeven pad, $VAULT_ROOT, de huidige map, de map
    boven dit script. De eerste die een `<marker>/`-submap heeft wint; is er
    geen enkele, dan de huidige map (de aanroeper meldt zelf wat er mist).
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (explicit, os.environ.get("VAULT_ROOT"), os.getcwd(), here):
        if cand and os.path.isdir(os.path.join(cand, marker)):
            return os.path.abspath(cand)
    return os.getcwd()


def vault_name(explicit=None):
    """Naam van de vault, zoals de map heet (bv. "HistVault")."""
    return os.path.basename(vault_root(explicit)) or "vault"


def env_suffix(explicit=None):
    """Het env-var-suffix van deze vault ("" voor ClaudeVault, "HIST", "GREEN")."""
    return VAULT_ENV_SUFFIX.get(vault_name(explicit).lower(), "")


def suffixed_names(base, explicit=None):
    """De env-varnamen voor `base`, meest specifiek eerst.

    `suffixed_names("BLOB_READ_WRITE_TOKEN")` geeft in HistVault
    ["BLOB_READ_WRITE_TOKEN_HIST", "BLOB_READ_WRITE_TOKEN"].
    """
    suffix = env_suffix(explicit)
    return ([f"{base}_{suffix}"] if suffix else []) + [base]


def config_candidates(name, explicit=None):
    """De configpaden voor `<naam>.env` in volgorde van voorkeur."""
    stem = os.path.splitext(name)[0].replace("-", "_").upper()
    out = []
    override = os.environ.get(f"VAULT_{stem}_ENV")
    if override:
        out.append(override)
    out.append(os.path.expanduser(f"~/.config/{vault_name(explicit).lower()}/{name}"))
    out.append(os.path.expanduser(f"~/.config/vaults/{name}"))
    out.append(os.path.expanduser(f"~/.config/histvault/{name}"))
    return out


def config_path(name, explicit=None):
    """Het eerste bestaande configpad, of het eerste kandidaatpad als er geen bestaat."""
    cands = config_candidates(name, explicit)
    return next((p for p in cands if os.path.exists(p)), cands[0])


def read_dotenv(path):
    """Lees een eenvoudig KEY=VALUE-bestand; aanhalingstekens eromheen gaan eraf."""
    out = {}
    if not path or not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                out[k.strip()] = v
    except OSError as e:
        sys.stderr.write(f"Kon {path} niet lezen: {e}\n")
    return out


def load_config(name, keys=(), explicit=None):
    """Credentials voor `<naam>.env`: bestand + environment variables.

    Een gezette environment variable gaat vóór het bestand, zodat een
    cloud-sessie of een eenmalige override altijd wint.
    """
    path = config_path(name, explicit)
    data = read_dotenv(path)
    data["_path"] = path
    for key in keys:
        for candidate in suffixed_names(key, explicit):
            if os.environ.get(candidate):
                data[key] = os.environ[candidate]
                break
            if candidate in data and candidate != key:
                data[key] = data[candidate]
                break
    return data


def vault_dotenv(name=".env.local", subdir="web", explicit=None):
    """Lees een `.env.local` uit de vault zelf (de Vercel-app in `web/`).

    Handig als laatste redmiddel voor tokens die alleen daar staan: lokaal is
    dat vaak het enige exemplaar.
    """
    root = vault_root(explicit)
    return read_dotenv(os.path.join(root, subdir, name) if subdir else os.path.join(root, name))
