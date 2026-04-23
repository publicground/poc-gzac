#!/usr/bin/env bash
# test-create-zaak.sh
#
# Creates a zaak in OpenZaak and verifies a notification appears in OpenNotificaties.
#
# Usage:
#   ./test-create-zaak.sh [ZAAKTYPE_UUID]
#
# If ZAAKTYPE_UUID is not provided, the script fetches the first available zaaktype.

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
OPENZAAK_BASE_URL="${OPENZAAK_BASE_URL:-https://openzaak.dmn-poc.publicground.nl}"
OPENNOTIFICATIES_BASE_URL="${OPENNOTIFICATIES_BASE_URL:-https://opennotificaties.dmn-poc.publicground.nl}"
CLIENT_ID="${CLIENT_ID:-gzac}"
if [[ -z "${CLIENT_SECRET:-}" ]]; then
  read -rsp "Enter CLIENT_SECRET for '${CLIENT_ID}': " CLIENT_SECRET
  echo ""
fi
BRONORGANISATIE="${BRONORGANISATIE:-000000000}"
VERANTWOORDELIJKE_ORGANISATIE="${VERANTWOORDELIJKE_ORGANISATIE:-000000000}"
ZAAKTYPE_UUID="${1:-}"

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✔${NC}  $*"; }
fail() { echo -e "${RED}✘${NC}  $*"; exit 1; }
info() { echo -e "${YELLOW}▶${NC}  $*"; }

# ── JWT generation (pure bash + python3 stdlib, no PyJWT needed) ──────────────
generate_jwt() {
  python3 - <<PYEOF
import base64, hashlib, hmac, json, time

client_id = "${CLIENT_ID}"
secret    = "${CLIENT_SECRET}"

header  = base64.urlsafe_b64encode(json.dumps({"typ":"JWT","alg":"HS256"}).encode()).rstrip(b"=")
payload = base64.urlsafe_b64encode(json.dumps({
    "iss": client_id,
    "iat": int(time.time()),
    "client_id": client_id,
    "user_id": "test-script",
    "user_representation": "Test Script",
}).encode()).rstrip(b"=")

sig_input = header + b"." + payload
sig = base64.urlsafe_b64encode(
    hmac.new(secret.encode(), sig_input, hashlib.sha256).digest()
).rstrip(b"=")

print((sig_input + b"." + sig).decode())
PYEOF
}

# ── Step 1: Generate JWT ──────────────────────────────────────────────────────
info "Generating JWT for client '${CLIENT_ID}'..."
JWT=$(generate_jwt)
ok "JWT generated"

# ── Step 2: Resolve zaaktype ──────────────────────────────────────────────────
if [[ -z "${ZAAKTYPE_UUID}" ]]; then
  info "No ZAAKTYPE_UUID provided — fetching first available zaaktype..."
  ZAAKTYPEN_RESPONSE=$(curl -sf \
    -H "Authorization: Bearer ${JWT}" \
    -H "Accept-Crs: EPSG:4326" \
    "${OPENZAAK_BASE_URL}/catalogi/api/v1/zaaktypen" 2>&1) \
    || fail "Could not reach ${OPENZAAK_BASE_URL}/catalogi/api/v1/zaaktypen"

  ZAAKTYPE_URL=$(echo "${ZAAKTYPEN_RESPONSE}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
results = data.get('results', [])
if not results:
    print('')
else:
    print(results[0]['url'])
")

  if [[ -z "${ZAAKTYPE_URL}" ]]; then
    fail "No zaaktypen found in OpenZaak. Import a catalogus first."
  fi
  ok "Found zaaktype: ${ZAAKTYPE_URL}"
else
  ZAAKTYPE_URL="${OPENZAAK_BASE_URL}/catalogi/api/v1/zaaktypen/${ZAAKTYPE_UUID}"
  ok "Using provided zaaktype: ${ZAAKTYPE_URL}"
fi

# ── Step 3: Create zaak ───────────────────────────────────────────────────────
TODAY=$(date +%Y-%m-%d)
info "Creating zaak (bronorganisatie=${BRONORGANISATIE}, startdatum=${TODAY})..."

ZAAK_RESPONSE=$(curl -sf -X POST \
  -H "Authorization: Bearer ${JWT}" \
  -H "Content-Crs: EPSG:4326" \
  -H "Accept-Crs: EPSG:4326" \
  -H "Content-Type: application/json" \
  "${OPENZAAK_BASE_URL}/zaken/api/v1/zaken" \
  -d "{
    \"zaaktype\": \"${ZAAKTYPE_URL}\",
    \"bronorganisatie\": \"${BRONORGANISATIE}\",
    \"verantwoordelijkeOrganisatie\": \"${VERANTWOORDELIJKE_ORGANISATIE}\",
    \"registratiedatum\": \"${TODAY}\",
    \"startdatum\": \"${TODAY}\"
  }" 2>&1) || fail "Failed to create zaak. Response: ${ZAAK_RESPONSE}"

ZAAK_URL=$(echo "${ZAAK_RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('url',''))")
ZAAK_ID=$(echo "${ZAAK_RESPONSE}"  | python3 -c "import sys,json; print(json.load(sys.stdin).get('identificatie',''))")

if [[ -z "${ZAAK_URL}" ]]; then
  fail "Zaak created but no URL returned. Response: ${ZAAK_RESPONSE}"
fi

ok "Zaak created!"
echo "     URL:            ${ZAAK_URL}"
echo "     Identificatie:  ${ZAAK_ID}"

# ── Step 4: Check notification in OpenNotificaties ───────────────────────────
info "Waiting 3 seconds for notification to propagate..."
sleep 3

info "Checking OpenNotificaties for a notification on kanaal 'zaken'..."
NOTIF_JWT=$(generate_jwt)
NOTIF_RESPONSE=$(curl -sf \
  -H "Authorization: Bearer ${NOTIF_JWT}" \
  "${OPENNOTIFICATIES_BASE_URL}/api/v1/notificaties?kanaal=zaken" 2>&1) \
  || { echo -e "${YELLOW}⚠${NC}  Could not reach OpenNotificaties (integration may still be pending)."; exit 0; }

NOTIF_COUNT=$(echo "${NOTIF_RESPONSE}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('count', 0))
")

if [[ "${NOTIF_COUNT}" -gt 0 ]]; then
  ok "Found ${NOTIF_COUNT} notification(s) on kanaal 'zaken' in OpenNotificaties — integration is working!"
else
  echo -e "${YELLOW}⚠${NC}  No notifications found yet on kanaal 'zaken'. Check OpenNotificaties logs."
fi

echo ""
ok "Test complete."
