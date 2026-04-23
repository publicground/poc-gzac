#!/usr/bin/env bash
# publish-catalogus.sh
#
# Publishes all concept resources in all catalogussen in OpenZaak:
#   informatieobjecttypen → besluittypen → zaaktypen
#
# Usage:
#   ./publish-catalogus.sh
#
# Environment variables (all optional except CLIENT_SECRET):
#   OPENZAAK_BASE_URL  — default: https://openzaak.dmn-poc.publicground.nl
#   CLIENT_ID          — default: gzac
#   CLIENT_SECRET      — required (prompted if not set)

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
OPENZAAK_BASE_URL="${OPENZAAK_BASE_URL:-https://openzaak.dmn-poc.publicground.nl}"
CLIENT_ID="${CLIENT_ID:-gzac}"
if [[ -z "${CLIENT_SECRET:-}" ]]; then
  read -rsp "Enter CLIENT_SECRET for '${CLIENT_ID}': " CLIENT_SECRET
  echo ""
fi

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✔${NC}  $*"; }
fail() { echo -e "${RED}✘${NC}  $*"; exit 1; }
info() { echo -e "${YELLOW}▶${NC}  $*"; }
skip() { echo -e "   ${NC}⏭  $*"; }

# ── JWT ───────────────────────────────────────────────────────────────────────
generate_jwt() {
  python3 - <<PYEOF
import base64, hashlib, hmac, json, time
client_id = "${CLIENT_ID}"
secret    = "${CLIENT_SECRET}"
header  = base64.urlsafe_b64encode(json.dumps({"typ":"JWT","alg":"HS256"}).encode()).rstrip(b"=")
payload = base64.urlsafe_b64encode(json.dumps({
    "iss": client_id, "iat": int(time.time()), "client_id": client_id,
    "user_id": "publish-script", "user_representation": "Publish Script",
}).encode()).rstrip(b"=")
sig_input = header + b"." + payload
sig = base64.urlsafe_b64encode(
    hmac.new(secret.encode(), sig_input, hashlib.sha256).digest()
).rstrip(b"=")
print((sig_input + b"." + sig).decode())
PYEOF
}

# ── Publish a single resource by URL ──────────────────────────────────────────
publish_resource() {
  local url="$1"
  local label="$2"
  local jwt="$3"

  local resp http_code
  resp=$(curl -s --max-time 15 -o /tmp/_pub_resp.txt -w "%{http_code}" -X POST \
    -H "Authorization: Bearer ${jwt}" "${url}/publish")
  http_code="${resp}"

  case "${http_code}" in
    200)
      ok "Published: ${label}"
      ;;
    400)
      local reason
      reason=$(python3 -c "import json; d=json.load(open('/tmp/_pub_resp.txt')); params=d.get('invalidParams',[]); print(params[0].get('reason','') if params else d.get('detail',''))" 2>/dev/null || cat /tmp/_pub_resp.txt)
      if echo "${reason}" | grep -q "al gepubliceerd\|already published\|Niet toegestaan"; then
        skip "Already published: ${label}"
      else
        echo -e "${RED}✘${NC}  Failed to publish ${label}: ${reason}"
        return 1
      fi
      ;;
    403)
      fail "403 Forbidden — gzac client lacks write permission on catalogi API. Check autorisaties in OpenZaak admin."
      ;;
    *)
      echo -e "${RED}✘${NC}  Unexpected HTTP ${http_code} for ${label}"
      cat /tmp/_pub_resp.txt
      return 1
      ;;
  esac
}

# ── Main ──────────────────────────────────────────────────────────────────────
info "Generating JWT..."
JWT=$(generate_jwt)
ok "JWT generated"

info "Fetching catalogussen from ${OPENZAAK_BASE_URL}..."
CATALOGUSSEN=$(curl -sf --max-time 15 \
  -H "Authorization: Bearer ${JWT}" \
  -H "Accept-Crs: EPSG:4326" \
  "${OPENZAAK_BASE_URL}/catalogi/api/v1/catalogussen") \
  || fail "Could not reach ${OPENZAAK_BASE_URL}/catalogi/api/v1/catalogussen"

CATALOGUS_COUNT=$(echo "${CATALOGUSSEN}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))")
if [[ "${CATALOGUS_COUNT}" -eq 0 ]]; then
  fail "No catalogussen found. Import a catalogus first."
fi
ok "Found ${CATALOGUS_COUNT} catalogus(sen)"

# Extract all resource URLs grouped by type
read -r -a IOTYPEN_URLS  < <(echo "${CATALOGUSSEN}" | python3 -c "
import sys,json; data=json.load(sys.stdin)
urls=[]
for cat in data.get('results',[]):
    urls.extend(cat.get('informatieobjecttypen',[]))
print(*urls)
")

read -r -a BESLUITTYPEN_URLS < <(echo "${CATALOGUSSEN}" | python3 -c "
import sys,json; data=json.load(sys.stdin)
urls=[]
for cat in data.get('results',[]):
    urls.extend(cat.get('besluittypen',[]))
print(*urls)
")

read -r -a ZAAKTYPEN_URLS < <(echo "${CATALOGUSSEN}" | python3 -c "
import sys,json; data=json.load(sys.stdin)
urls=[]
for cat in data.get('results',[]):
    urls.extend(cat.get('zaaktypen',[]))
print(*urls)
")

# 1. Informatieobjecttypen
if [[ ${#IOTYPEN_URLS[@]} -gt 0 ]]; then
  info "Publishing ${#IOTYPEN_URLS[@]} informatieobjecttype(n)..."
  JWT=$(generate_jwt)  # refresh JWT before batch
  for url in "${IOTYPEN_URLS[@]}"; do
    uuid="${url##*/}"
    publish_resource "${url}" "informatieobjecttype ${uuid}" "${JWT}" || true
  done
fi

# 2. Besluittypen
if [[ ${#BESLUITTYPEN_URLS[@]} -gt 0 ]]; then
  info "Publishing ${#BESLUITTYPEN_URLS[@]} besluittype(n)..."
  JWT=$(generate_jwt)
  for url in "${BESLUITTYPEN_URLS[@]}"; do
    uuid="${url##*/}"
    publish_resource "${url}" "besluittype ${uuid}" "${JWT}" || true
  done
fi

# 3. Zaaktypen (last — depends on the above)
if [[ ${#ZAAKTYPEN_URLS[@]} -gt 0 ]]; then
  info "Publishing ${#ZAAKTYPEN_URLS[@]} zaaktype(n)..."
  JWT=$(generate_jwt)
  for url in "${ZAAKTYPEN_URLS[@]}"; do
    uuid="${url##*/}"
    publish_resource "${url}" "zaaktype ${uuid}" "${JWT}"
  done
fi

echo ""
ok "Done. All resources published."
