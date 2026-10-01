#!/usr/bin/env bash
# Rebuilds the stack from scratch, runs every demo scenario from the brief and
# the full pytest suite, and saves the raw commands + output to
# docs/evidence/evidence-<timestamp>.txt. Needs Docker (with the compose plugin).
set -u
cd "$(dirname "$0")/.."
OUT="docs/evidence/evidence-$(date +%Y%m%d-%H%M%S).txt"
KEY=partilon-api-key-2026
GW=http://127.0.0.1:9080
AG=http://127.0.0.1:8010
JSON='Content-Type: application/json'

run() { echo; echo "\$ $*"; eval "$@" 2>&1; echo "[exit $?]"; }
section() { echo; echo "================================================================"; echo "## $1"; echo "================================================================"; }
ask() { printf '{"query":"%s"}' "$1"; }
# POST a query; print HTTP status, then the pretty-printed JSON body.
agent() { local body; body=$(mktemp); echo "# same as: curl -X POST $AG/agent/query -H '$JSON' ${*:2} -d '$(ask "$1")'"; curl -s -o "$body" -w 'HTTP %{http_code}\n' -X POST "$AG/agent/query" -H "$JSON" "${@:2}" -d "$(ask "$1")"; python3 -m json.tool --no-ensure-ascii < "$body"; rm -f "$body"; }

{
echo "Partilon evidence run: $(date -u +%Y-%m-%dT%H:%M:%SZ)  commit: $(git rev-parse --short HEAD 2>/dev/null)"
section "1. Clean start (no volumes, no prior state)"
run docker compose down -v
run docker compose up --build -d
sleep 20
run "docker compose ps -a --format 'table {{.Name}}\t{{.Status}}'"
run docker logs partilon-apisix-provision

section "1b. Customer API through the gateway (valid API key)"
run "curl -s -w '\n[HTTP %{http_code}]\n' -H 'apikey: $KEY' $GW/api/customers/C001"

section "2. Retrieve a customer (single capability)"
run agent "'Show customer C001'"
sleep 11
section "3. Customer + latest order (multi-API orchestration, multi-agent)"
run agent "'Find customer C001 and tell me their latest order status'" -H "'X-Correlation-ID: EVIDENCE-TRACE-001'"
section "4. Unknown customer (no fabricated data)"
sleep 11
run agent "'Show customer C999'"
section "5. Order through agent delegation (A2A)"
run agent "'What is the status of order O1001?'"
section "6. Unauthorized request (security)"
run "curl -s -w '\n[HTTP %{http_code}]\n' $GW/api/customers/C001"
section "7. Excessive requests (traffic management: limit 5 per 10 s)"
sleep 11
run "for i in 1 2 3 4 5 6 7; do curl -s -o /dev/null -w \"request \$i -> HTTP %{http_code}\n\" -H 'apikey: $KEY' $GW/api/customers/C001; done"
section "8. Trace one request across all components (ID EVIDENCE-TRACE-001)"
sleep 2
for c in coordinator-agent customer-agent order-agent apisix customer-service order-service; do
  run "docker logs partilon-$c 2>&1 | grep EVIDENCE-TRACE-001 | cut -c1-230"
done
section "9. Failure handling (container really stopped, then restarted)"
sleep 11
run "docker stop partilon-order-agent"
run agent "'Find customer C001 and tell me their latest order status'"
run "docker start partilon-order-agent"
run "docker stop partilon-customer-service"
sleep 2
run agent "'Show customer C001'"
run "docker start partilon-customer-service"
sleep 6
run docker compose restart apisix
sleep 10
section "10. Automated test suite (pytest -v, against the live stack)"
P="$PWD"
run "docker run --rm --network host --group-add \$(getent group docker | cut -d: -f3) -v /var/run/docker.sock:/var/run/docker.sock -v /usr/bin/docker:/usr/bin/docker:ro -v /usr/libexec/docker/cli-plugins:/usr/libexec/docker/cli-plugins:ro -v '$P:$P' -w '$P/tests' python:3.12-slim sh -c 'pip install -q -r requirements.txt >/dev/null 2>&1; pytest -v -p no:cacheprovider'"
} > "$OUT" 2>&1
echo "Evidence saved to $OUT"
