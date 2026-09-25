import json
import os
import re
import sys
import uuid
from pathlib import Path

import django.apps
from django.db import transaction
from vng_api_common.models import JWTSecret
from vng_api_common.authorizations.models import Applicatie, Autorisatie

# Stream stdout/stderr direct zonder buffering voor K8s pod logs
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# 1. BASE_URL ophalen uit de omgevingsvariabele
BASE_URL = os.getenv("BASE_URL", "http://openzaak.wigo4it.nl").rstrip("/")
print(f"[INFO] Gebruikte BASE_URL: {BASE_URL}")

# Modellen ophalen uit Django registry
ZaakType = None
InformatieObjectType = None
BesluitType = None
Catalogus = None

for app_label in ["ztc", "zrc", "drc", "brc", "catalogi"]:
    try:
        if not ZaakType:
            ZaakType = django.apps.apps.get_model(app_label, "ZaakType")
        if not InformatieObjectType:
            InformatieObjectType = django.apps.apps.get_model(app_label, "InformatieObjectType")
        if not BesluitType:
            BesluitType = django.apps.apps.get_model(app_label, "BesluitType")
        if not Catalogus:
            Catalogus = django.apps.apps.get_model(app_label, "Catalogus")
    except LookupError:
        continue

# Zoek config.json op bekende locaties
POSSIBLE_PATHS = [
    Path("/app/autorisaties/config.json"),
    Path("./autorisaties/config.json"),
    Path("./config.json"),
]

CONFIG_PATH = None
for path in POSSIBLE_PATHS:
    if path.exists():
        CONFIG_PATH = path
        break

if not CONFIG_PATH:
    print(f"[ERROR] Geen config.json gevonden op locaties: {[str(p) for p in POSSIBLE_PATHS]}")
    sys.exit(1)

print(f"[INFO] Configuratie inlezen van: {CONFIG_PATH}")

try:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        apps_config = json.load(f)
except Exception as e:
    print(f"[ERROR] Kon {CONFIG_PATH} niet inlezen of parsen: {e}")
    sys.exit(1)


def resolve_secret_value(secret_raw, client_id):
    """
    Controleert of een secret een omgevingsvariabele referentie is (bijv. ${MY_SECRET_ENV}).
    Zo ja, wordt de waarde uit os.environ gehaald.
    Breekt af met sys.exit(1) als de omgevingsvariabele niet aanwezig of leeg is.
    """
    if not secret_raw:
        print(f"[ERROR] Geen secret opgegeven voor client '{client_id}'!")
        sys.exit(1)

    match = re.match(r"^\$\{([A-Za-z0-9_]+)\}$", secret_raw.strip())
    if match:
        env_var_name = match.group(1)
        resolved_value = os.getenv(env_var_name)
        if not resolved_value:
            print(f"[ERROR] Secret voor client '{client_id}' verwijst naar omgevingsvariabele '{env_var_name}', maar deze is niet aanwezig of leeg!")
            sys.exit(1)
        print(f"  [INFO] Secret voor '{client_id}' succesvol opgehaald uit omgevingsvariabele '${{{env_var_name}}}'.")
        return resolved_value

    return secret_raw


def extract_uuid(url_or_uuid):
    """Extraheert een geldige UUID uit een URL of losse UUID-string."""
    if not url_or_uuid:
        return None
    raw_str = url_or_uuid.strip("/").split("/")[-1]
    try:
        return str(uuid.UUID(raw_str))
    except ValueError:
        return None


def normalize_resource_url(input_val, resource_name, model_class=None):
    """
    Zorgt ervoor dat input_val altijd een volledige, geldige URL is.
    Als input_val een losse UUID is (of een URL met een afwijkend domein),
    wordt deze omgevormd naar de actuele BASE_URL.
    Indien een model_class is meegegeven, wordt gecontroleerd of het object in de DB bestaat.
    """
    if not input_val:
        return ""

    val_uuid = extract_uuid(input_val)
    if val_uuid:
        if model_class:
            exists = model_class.objects.filter(uuid=val_uuid).exists()
            if not exists:
                print(f"[ERROR] Opgegeven {resource_name} met UUID/URL '{input_val}' bestaat niet in de database!")
                sys.exit(1)

        return f"{BASE_URL}/catalogi/api/v1/{resource_name}/{val_uuid}"

    return input_val


def get_full_object_url(obj, resource_name):
    """
    Bouwt de exacte, geldige interne API-URL voor een DB-object
    met gebruik van de ingestelde BASE_URL.
    """
    obj_uuid = getattr(obj, "uuid", None)

    if hasattr(obj, "get_absolute_url"):
        try:
            url = obj.get_absolute_url()
            if url.startswith("http"):
                path = "/" + url.split("/", 3)[-1]
                return f"{BASE_URL}{path}"
            return f"{BASE_URL}{url}"
        except Exception:
            pass

    if hasattr(obj, "url") and obj.url and obj.url.startswith("http"):
        path = "/" + obj.url.split("/", 3)[-1]
        return f"{BASE_URL}{path}"

    if obj_uuid:
        return f"{BASE_URL}/catalogi/api/v1/{resource_name}/{obj_uuid}"

    return str(obj)


def get_catalogus_object(catalogus_input):
    """Zoekt de Catalogus instantie op in de DB via UUID of URL."""
    if not Catalogus or not catalogus_input:
        return None
    
    cat_uuid = extract_uuid(catalogus_input)
    if cat_uuid:
        obj = Catalogus.objects.filter(uuid=cat_uuid).first()
        if obj:
            return obj
            
    try:
        return Catalogus.objects.filter(url=catalogus_input).first()
    except Exception:
        return None


def get_objects_for_catalogus(model_class, catalogus_input, rel_name):
    """Generieke ophaalfunctie voor typen gekoppeld aan een catalogus."""
    if not model_class:
        return []

    cat_obj = get_catalogus_object(catalogus_input)
    cat_uuid = extract_uuid(catalogus_input)

    if cat_obj:
        if hasattr(cat_obj, rel_name):
            qs = getattr(cat_obj, rel_name).all()
            if qs.exists():
                return list(qs)
        try:
            return list(model_class.objects.filter(catalogus=cat_obj))
        except Exception:
            pass

    # Fallback: handmatig vergelijken als FK lookup faalt
    all_items = model_class.objects.all()
    matching = []
    for item in all_items:
        cat_val = getattr(item, "catalogus", None)
        if cat_val is None:
            continue
        cat_str = str(cat_val)
        cat_url_attr = getattr(cat_val, "url", "")
        cat_uuid_attr = str(getattr(cat_val, "uuid", ""))

        if (
            (cat_uuid and cat_uuid in cat_str)
            or (catalogus_input and catalogus_input in cat_str)
            or (catalogus_input and catalogus_input == cat_url_attr)
            or (cat_uuid and cat_uuid == cat_uuid_attr)
        ):
            matching.append(item)

    return matching


def apply_autorisatie(app, component, scopes, max_vertrouwelijkheid, zaaktype="", info_type="", besluit_type=""):
    """Helper-functie om een Autorisatie aan te maken/bij te werken en het object te retourneren."""
    filter_kwargs = {
        "applicatie": app,
        "component": component,
        "zaaktype": zaaktype,
        "informatieobjecttype": info_type,
        "besluittype": besluit_type,
    }

    autorisatie, _ = Autorisatie.objects.get_or_create(
        **filter_kwargs,
        defaults={
            "scopes": scopes,
            "max_vertrouwelijkheidaanduiding": max_vertrouwelijkheid
        }
    )

    autorisatie.scopes = scopes
    autorisatie.max_vertrouwelijkheidaanduiding = max_vertrouwelijkheid
    autorisatie.save()
    return autorisatie


# Uitvoeren binnen een atomaire transactie: bij een exception wordt alles teruggerold
try:
    with transaction.atomic():
        for app_data in apps_config:
            client_id = app_data.get("client_id")
            secret_raw = app_data.get("secret")
            label = app_data.get("label")
            heeft_alle_autorisaties = app_data.get("heeft_alle_autorisaties", False)
            authorizations = app_data.get("authorizations", [])

            if not client_id or not label:
                print(f"[ERROR] Onvolledige applicatieconfiguratie (client_id of label ontbreekt): {app_data}")
                sys.exit(1)

            print(f"\n--- Verwerken applicatie: '{label}' ({client_id}) ---")

            # Secret uitlezen/resolven uit omgevingsvariabele
            secret = resolve_secret_value(secret_raw, client_id)

            # 1. JWTSecret
            jwt_secret, secret_created = JWTSecret.objects.get_or_create(
                identifier=client_id,
                defaults={"secret": secret}
            )
            if not secret_created and jwt_secret.secret != secret:
                jwt_secret.secret = secret
                jwt_secret.save()

            # 2. Applicatie
            app, app_created = Applicatie.objects.get_or_create(
                label=label,
                defaults={
                    "heeft_alle_autorisaties": heeft_alle_autorisaties,
                    "client_ids": [client_id],
                }
            )

            # Zorg dat wijzigingen aan een bestaande applicatie worden opgeslagen
            if not app_created:
                if app.heeft_alle_autorisaties != heeft_alle_autorisaties:
                    app.heeft_alle_autorisaties = heeft_alle_autorisaties
                    print(f"  [INFO] 'heeft_alle_autorisaties' bijgewerkt naar {heeft_alle_autorisaties}.")
                
                if app.client_ids is None:
                    app.client_ids = []
                if client_id not in app.client_ids:
                    app.client_ids.append(client_id)
                app.save()

            # 3. Autorisaties beheren
            if heeft_alle_autorisaties:
                # Situatie 1: heeft_alle_autorisaties = True -> VERWIJDER alle specifieke autorisaties
                deleted_count, _ = Autorisatie.objects.filter(applicatie=app).delete()
                if deleted_count > 0:
                    print(f"  [INFO] {deleted_count} oude specifieke autorisatie(s) opgeruimd vanwege 'heeft_alle_autorisaties=True'.")
                else:
                    print("  [INFO] 'heeft_alle_autorisaties=True' ingesteld (geen losse autorisaties aanwezig).")
            else:
                # Situatie 2: heeft_alle_autorisaties = False -> Verwerk opgegeven autorisaties
                valid_auth_ids = []

                for auth_data in authorizations:
                    if not isinstance(auth_data, dict):
                        print(f"  [WARNING] Autorisatie element overgeslagen (ongeldig formaat): {auth_data}")
                        continue

                    component = auth_data.get("component")
                    scopes = auth_data.get("scopes", [])
                    zaaktype_input = auth_data.get("zaaktype", "")
                    info_type_input = auth_data.get("informatieobjecttype", "")
                    besluit_type_input = auth_data.get("besluittype", "")
                    catalogus_input = auth_data.get("catalogus", "")
                    max_vertrouwelijkheid = auth_data.get("max_vertrouwelijkheidaanduiding", "openbaar")

                    if catalogus_input:
                        if component == "zrc":
                            items = get_objects_for_catalogus(ZaakType, catalogus_input, "zaaktypen")
                            if not items:
                                print(f"[ERROR] Geen ZaakTypen gevonden voor catalogus '{catalogus_input}'!")
                                sys.exit(1)
                            for item in items:
                                url = get_full_object_url(item, "zaaktypen")
                                aut = apply_autorisatie(app, component, scopes, max_vertrouwelijkheid, zaaktype=url)
                                valid_auth_ids.append(aut.id)

                        elif component == "drc":
                            items = get_objects_for_catalogus(InformatieObjectType, catalogus_input, "informatieobjecttypen")
                            if not items:
                                print(f"[ERROR] Geen InformatieObjectTypen gevonden voor catalogus '{catalogus_input}'!")
                                sys.exit(1)
                            for item in items:
                                url = get_full_object_url(item, "informatieobjecttypen")
                                aut = apply_autorisatie(app, component, scopes, max_vertrouwelijkheid, info_type=url)
                                valid_auth_ids.append(aut.id)

                        elif component == "brc":
                            items = get_objects_for_catalogus(BesluitType, catalogus_input, "besluittypen")
                            if not items:
                                print(f"[ERROR] Geen BesluitTypen gevonden voor catalogus '{catalogus_input}'!")
                                sys.exit(1)
                            for item in items:
                                url = get_full_object_url(item, "besluittypen")
                                aut = apply_autorisatie(app, component, scopes, max_vertrouwelijkheid, besluit_type=url)
                                valid_auth_ids.append(aut.id)
                    else:
                        final_zaaktype = normalize_resource_url(zaaktype_input, "zaaktypen", ZaakType)
                        final_info_type = normalize_resource_url(info_type_input, "informatieobjecttypen", InformatieObjectType)
                        final_besluit_type = normalize_resource_url(besluit_type_input, "besluittypen", BesluitType)

                        aut = apply_autorisatie(
                            app=app,
                            component=component,
                            scopes=scopes,
                            max_vertrouwelijkheid=max_vertrouwelijkheid,
                            zaaktype=final_zaaktype,
                            info_type=final_info_type,
                            besluit_type=final_besluit_type
                        )
                        valid_auth_ids.append(aut.id)

                # Controleer of er wel autorisaties zijn aangemaakt als heeft_alle_autorisaties = False
                if not valid_auth_ids:
                    print(f"[ERROR] Applicatie '{label}' heeft 'heeft_alle_autorisaties=False', maar er zijn geen geldige autorisaties opgegeven!")
                    sys.exit(1)

                # Ruim eventuele overtollige/oude autorisaties op die niet meer in config.json staan
                stale_deleted, _ = Autorisatie.objects.filter(applicatie=app).exclude(id__in=valid_auth_ids).delete()
                if stale_deleted > 0:
                    print(f"  [INFO] {stale_deleted} verouderde autorisatie(s) opgeruimd.")

except Exception as err:
    print(f"\n[ERROR] Er is een onverwachte fout opgetreden: {err}")
    sys.exit(1)

print("\n[SUCCESS] Alle applicaties en autorisaties succesvol verwerkt!")
sys.exit(0)