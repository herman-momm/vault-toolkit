#!/usr/bin/env python3
"""
gemini-fallback.py — Zero-dependency Gemini API fallback caller voor vault-taken.
"""
import sys
import os
import json
import ssl
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultenv


def load_env():
    """Zet de sleutels uit claude.env in de omgeving (bestaande waarden blijven staan)."""
    for k, v in vaultenv.read_dotenv(vaultenv.config_path("claude.env")).items():
        if k != "_path":
            os.environ.setdefault(k, v)

def main():
    load_env()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(f"GEMINI_API_KEY niet gevonden in de omgeving of in "
              f"{vaultenv.config_path('claude.env')}", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Gebruik: gemini-fallback.py <prompt_file_of_tekst>", file=sys.stderr)
        sys.exit(1)

    input_arg = sys.argv[1]
    if os.path.exists(input_arg):
        with open(input_arg, "r", encoding="utf-8") as f:
            prompt = f.read()
    else:
        prompt = input_arg

    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
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

    # De CA-bundel van deze Mac wordt door vaultenv opgezocht (certifi of de
    # systeembundel), in plaats van de certificaatcontrole uit te zetten zodra
    # verificatie één keer faalt — wat hier eerder gebeurde bij élke
    # uitzondering, inclusief een time-out.
    response_obj = urllib.request.urlopen(req, timeout=120,
                                          context=vaultenv.tls_context())

    try:
        with response_obj as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            candidates = res_data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                print(text)
            else:
                print("Geen respons van Gemini API", file=sys.stderr)
                sys.exit(1)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"Gemini API Fout {e.code}: {err_body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Fout bij Gemini API-aanroep: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()


