import time
import json
import os
import glob
import jwt
import requests

# ------------------------------------------------------------------------------
# CONFIGURATIE
# ------------------------------------------------------------------------------
secret_key = os.environ.get('OPENZAAK_SECRET_KEY', 'dummy')
openzaak_url = os.environ.get('OPENZAAK_URL', 'http://openzaak.wigo4it.nl')

# Bepaal het absolute pad van de map waarin DIT script (set-permissions.py) staat (bijv. /scripts)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Als CONFIG_DIR niet als environment variable is meegegeven,
# gebruik dan automatisch de 'config' submap náást dit script (bijv. /scripts/config)
DEFAULT_CONFIG_DIR = os.path.join(SCRIPT_DIR, 'config')
CONFIG_DIR = os.environ.get('CONFIG_DIR', DEFAULT_CONFIG_DIR)

search_path = os.path.join(CONFIG_DIR, '*.json')

# ------------------------------------------------------------------------------
# 1. BEHEERDER JWT GENEREREN
# ------------------------------------------------------------------------------
print("1. Beheerder JWT genereren...")
payload = {
    "iss": "open-zaak",
    "iat": int(time.time()),
    "exp": int(time.time()) + 300,
    "client_id": "open-zaak",
    "user_id": "gitops-admin",
    "user_representation": "GitOps Admin Job"
}

token = jwt.encode(payload, secret_key, algorithm="HS256")
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# ------------------------------------------------------------------------------
# 2. ALLE BESTAANDE APPLICATIES OPHALEN (INCL. PAGINERING)
# ------------------------------------------------------------------------------
print("\n2. Alle bestaande applicaties ophalen uit OpenZaak...")
all_applications = []
next_url = f"{openzaak_url}/autorisaties/api/v1/applicaties"

while next_url:
    res = requests.get(next_url, headers=headers)
    if res.status_code == 200:
        data = res.json()
        all_applications.extend(data.get('results', []))
        next_url = data.get('next')  # Blijf doorlopen zolang er nog pagina's zijn
    else:
        print(f"[ERROR] Kan applicaties niet ophalen (Status {res.status_code}):")
        print(res.text)
        exit(1)

print(f"[INFO] Totaal {len(all_applications)} applicatie(s) opgehaald uit OpenZaak.")

# ------------------------------------------------------------------------------
# 3. AUTORISATIES PATCHEN PER JSON-BESTAND
# ------------------------------------------------------------------------------
json_files = glob.glob(search_path)
print(f"\n3. Zoeken naar JSON-bestanden via '{search_path}'... ({len(json_files)} bestand(en) gevonden)")

if not json_files:
    print(f"[WAARSCHUWING] Geen JSON-bestanden gevonden in '{CONFIG_DIR}'. Controleer het map-pad.")

for file_path in json_files:
    target_client_id = os.path.basename(file_path).replace('.json', '')
    app_uuid = None

    print(f"\n--------------------------------------------------")
    print(f"[PROCESSING] Verwerken van '{target_client_id}.json'...")

    # Zoek in de lokaal opgehaalde lijst naar de applicatie met de matchende client_id
    for app in all_applications:
        # Ondersteun zowel lijsten van strings als lijsten van dicts (voor backwards compatibility)
        raw_client_ids = app.get('clientIds', [])
        client_ids_in_app = [
            c.get('clientId') if isinstance(c, dict) else str(c) 
            for c in raw_client_ids
        ]

        if target_client_id in client_ids_in_app or app.get('label') == target_client_id:
            app_uuid = app['url'].rstrip('/').split('/')[-1]
            break

    if not app_uuid:
        print(f"[ERROR] Geen applicatie gevonden in OpenZaak met Client ID: '{target_client_id}'")
        print(f"[SKIP] Overslaan van '{target_client_id}.json'")
        continue

    print(f"[INFO] Applicatie voor '{target_client_id}' gevonden! UUID: {app_uuid}")

    # JSON-payload inlezen voor de autorisaties
    with open(file_path, 'r') as f:
        autorisatie_payload = json.load(f)

    # Voer de PATCH uit naar het specifieke applicatie-endpoint met de verkregen UUID
    patch_url = f"{openzaak_url}/autorisaties/api/v1/applicaties/{app_uuid}"
    print(f"[INFO] Autorisaties patchen via: {patch_url}")
    response = requests.patch(patch_url, json=autorisatie_payload, headers=headers)

    if response.status_code in [200, 204]:
        print(f"[OK] Autorisaties voor '{target_client_id}' (UUID: {app_uuid}) succesvol bijgewerkt.")
    else:
        print(f"[ERROR] Fout bij patchen van '{target_client_id}' (Status {response.status_code}):")
        print(response.text)

print("\nScript verwerking afgerond.")