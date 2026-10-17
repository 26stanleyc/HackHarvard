#!/usr/bin/env bash
# Scripted curl run of every API endpoint (M6). Boots nothing: start the server first, e.g.
#   (cd backend && uv run uvicorn clearinghouse.api:app --port 8000)
# Usage: backend/scripts/smoke_api.sh [BASE]   (BASE defaults to $BASE or http://localhost:8000)
# Checks status codes, prints timings, exits non-zero on any failure.
set -uo pipefail

BASE="${1:-${BASE:-http://localhost:8000}}"
BASE="${BASE%/}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURES="$(cd "$HERE/../../fixtures" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILED=0

check() {  # check NAME EXPECTED_STATUS METHOD PATH [BODY_FILE]
  local name="$1" expect="$2" method="$3" path="$4" body="${5:-}"
  local out="$TMP/$name.json" meta
  if [[ -n "$body" ]]; then
    meta=$(curl -sS -o "$out" -w '%{http_code} %{time_total}' -X "$method" \
      -H 'Content-Type: application/json' --data-binary "@$body" "$BASE$path" 2>"$TMP/err")
  else
    meta=$(curl -sS -o "$out" -w '%{http_code} %{time_total}' -X "$method" "$BASE$path" \
      2>"$TMP/err")
  fi
  local code="${meta%% *}" secs="${meta##* }"
  if [[ "$code" == "$expect" ]]; then
    printf 'PASS  %-6s %-22s -> %s  %6.3f s  %7d bytes\n' "$method" "$path" "$code" "$secs" \
      "$(wc -c <"$out")"
  else
    printf 'FAIL  %-6s %-22s -> %s (expected %s)  %s\n' "$method" "$path" "${code:-none}" \
      "$expect" "$(head -c 300 "$out" 2>/dev/null) $(cat "$TMP/err")"
    FAILED=1
  fi
}

echo "smoke test against $BASE"
check health 200 GET /api/health
check meta 200 GET /api/meta
check presets 200 GET /api/presets
check adoption 200 GET /api/adoption-chart
check clear 200 POST /api/clear "$FIXTURES/clear_request.json"
check evaluate 200 POST /api/evaluate "$FIXTURES/evaluate_request.json"

# One infeasible bid (energy far above max_mw x window) must be rejected with 422 per bid.
sed 's/"energy_mwh": 2000.0/"energy_mwh": 999999.0/' "$FIXTURES/clear_request.json" \
  >"$TMP/infeasible_request.json"
check infeasible 422 POST /api/clear "$TMP/infeasible_request.json"
if ! grep -q '"bid_id":"b1"' "$TMP/infeasible.json" 2>/dev/null; then
  echo "FAIL  infeasible clear: 422 body has no per-bid error for b1: $(head -c 300 "$TMP/infeasible.json" 2>/dev/null)"
  FAILED=1
else
  echo "      422 detail: $(head -c 200 "$TMP/infeasible.json")"
fi

if [[ "$FAILED" == 0 ]]; then
  echo "all endpoints OK"
else
  echo "SMOKE TEST FAILED" >&2
fi
exit "$FAILED"
