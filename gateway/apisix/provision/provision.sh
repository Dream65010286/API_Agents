#!/bin/sh
# Provisions the APISIX routes, upstreams, API-key consumer, and
# rate-limit plugin that this stack needs. These objects live only in
# APISIX's etcd-backed config store -- nothing in gateway/apisix/config/
# declares them, so this script is what makes a clean `docker compose up`
# (fresh etcd volume, no prior state) produce a working gateway.
#
# Runs with `network_mode: "service:apisix"` (see docker-compose.yml), so
# it talks to the Admin API over the apisix container's own loopback
# interface (127.0.0.1:9180). That's always covered by the
# `allow_admin: 127.0.0.0/24` rule in gateway/apisix/config/config.yaml,
# regardless of what subnet Docker hands the compose network -- no
# change to that file was needed.
#
# Every call is a PUT to a fixed resource ID, so re-running this script
# (e.g. a second `docker compose up` without `-v`) re-applies the same
# config instead of creating duplicates.

set -eu

ADMIN_URL="http://127.0.0.1:9180/apisix/admin"
ADMIN_KEY="${ADMIN_KEY:?ADMIN_KEY is required}"
API_KEY="${API_KEY:?API_KEY is required}"

echo "[provision] waiting for the APISIX Admin API..."
attempt=0
until [ "$(curl -s -o /dev/null -w '%{http_code}' -H "X-API-KEY: ${ADMIN_KEY}" "${ADMIN_URL}/routes")" = "200" ]; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo "[provision] ERROR: Admin API did not become ready after ${attempt}s" >&2
    exit 1
  fi
  sleep 1
done
echo "[provision] Admin API is up (after ${attempt}s)."

apply() {
  # $1 = admin path, e.g. /upstreams/customer-service
  # $2 = JSON request body
  response_file="/tmp/provision-response.json"
  code=$(curl -s -o "$response_file" -w '%{http_code}' \
    -X PUT "${ADMIN_URL}${1}" \
    -H "X-API-KEY: ${ADMIN_KEY}" \
    -H "Content-Type: application/json" \
    -d "$2")

  case "$code" in
    200|201)
      echo "[provision] OK   PUT ${1} -> ${code}"
      ;;
    *)
      echo "[provision] FAIL PUT ${1} -> ${code}" >&2
      cat "$response_file" >&2
      exit 1
      ;;
  esac
}

echo "[provision] applying upstreams..."

apply "/upstreams/customer-service" '{
  "type": "roundrobin",
  "nodes": {"customer-service:8000": 1}
}'

apply "/upstreams/order-service" '{
  "type": "roundrobin",
  "nodes": {"order-service:8000": 1}
}'

echo "[provision] applying API-key consumer..."

apply "/consumers" "$(cat <<EOF
{
  "username": "partilon-client",
  "plugins": {
    "key-auth": {"key": "${API_KEY}"}
  }
}
EOF
)"

echo "[provision] applying routes..."

# All three routes match on a wildcard prefix (APISIX's default
# "radixtree_host_uri" router does not support ":name" path-parameter
# capture -- that requires a non-default router, which would mean
# changing gateway/apisix/config/config.yaml's router mode. Wildcard
# prefix + the proxy-rewrite plugin's "regex_uri" works with the
# default router instead, so no gateway config file needed to change.)
#
# customer-orders-api has a higher "priority" than customer-api so that
# a 3-segment path (".../{id}/orders") is matched here, not by the
# 2-segment customer-api route below -- both share the same uri prefix.

# Nested "customer's orders" lookup -- order-service, not rate limited.
apply "/routes/customer-orders-api" '{
  "uri": "/api/customers/*",
  "methods": ["GET"],
  "priority": 10,
  "vars": [["uri", "~~", "^/api/customers/[^/]+/orders$"]],
  "upstream_id": "order-service",
  "plugins": {
    "key-auth": {},
    "proxy-rewrite": {
      "regex_uri": ["^/api/customers/(.+)/orders$", "/customers/$1/orders"]
    }
  }
}'

# Single-customer lookup -- customer-service, rate limited
# (5 requests / 10 seconds per consumer; must match
# tests/conftest.py's CUSTOMER_API_RATE_LIMIT_COUNT/WINDOW).
apply "/routes/customer-api" '{
  "uri": "/api/customers/*",
  "methods": ["GET"],
  "priority": 0,
  "upstream_id": "customer-service",
  "plugins": {
    "key-auth": {},
    "proxy-rewrite": {
      "regex_uri": ["^/api/customers/(.+)$", "/customers/$1"]
    },
    "limit-count": {
      "count": 5,
      "time_window": 10,
      "key_type": "var",
      "key": "consumer_name",
      "rejected_code": 429
    }
  }
}'

# Single-order lookup -- order-service, not rate limited.
apply "/routes/order-api" '{
  "uri": "/api/orders/*",
  "methods": ["GET"],
  "upstream_id": "order-service",
  "plugins": {
    "key-auth": {},
    "proxy-rewrite": {
      "regex_uri": ["^/api/orders/(.+)$", "/orders/$1"]
    }
  }
}'

echo "[provision] done: upstreams, consumer, and routes are provisioned."
