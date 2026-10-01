# Demo Script

Ten scripted scenarios, with exact commands and expected results, that
exercise every layer of the system: direct REST, the API Gateway, agent
discovery, A2A delegation, multi-agent coordination, the documented
failure paths, rate limiting, authentication, and correlation-ID
tracing. The same requests are also available as a ready-to-run
collection: [postman/](../postman/).

## Prerequisites

```
docker compose up --build -d
```

Then wait for the stack to actually be ready (there is no single
"all ready" signal from Compose itself — see
[docs/architecture.md → Startup ordering](architecture.md#startup-ordering-and-readiness)):

```bash
# repeat until this returns 401 (route loaded, key-auth working) rather
# than a connection error or a 404
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9080/api/customers/C001
```

**Gateway routes are now provisioned automatically.** A one-shot
`apisix-provision` service (see
[docs/architecture.md](architecture.md#apisix-provisioning-declarative-script-applied-automatically))
applies the routes/upstreams/API-key consumer on every `docker compose
up`, including against a completely fresh `etcd` volume (first-ever
`docker compose up`, or after a `docker compose down -v`) — no manual
Admin API step is needed. If you do hit `404 Not Found` on every gateway
call below while `http://localhost:8001/health` /
`http://localhost:8002/health` work fine directly, check
`docker compose logs apisix-provision` first — it logs each resource it
applies and will fail loudly (non-zero exit) if the Admin API ever
rejects one.

Default API key used throughout: `partilon-api-key-2026`.

**Rate-limit awareness:** scenario 8 and several other scenarios share a
single budget of **5 requests / 10 seconds** against the
`/api/customers/{id}` gateway route (see
[README → Rate limiting](../README.md#rate-limiting)). The scenarios
below are ordered to avoid colliding with each other, but if you run
them out of order, or repeat one, you may see an unexpected `429` —
that's the shared budget, not a bug. Wait 10+ seconds and retry.

---

## 1. Retrieve customer

Direct REST, through the gateway, authenticated:

```bash
curl -s http://localhost:9080/api/customers/C001 \
  -H "apikey: partilon-api-key-2026" | jq
```

Expected:
```json
{"customer_id": "C001", "name": "Alice Johnson", "email": "alice@example.com"}
```

(Also works unauthenticated, directly against the service, for
comparison/debugging: `curl -s http://localhost:8001/customers/C001` —
see [README → Known limitations](../README.md#known-limitations) on why
that bypass exists.)

## 2. Customer + latest order (agentic, single call)

```bash
curl -s -X POST http://localhost:8010/agent/query \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: demo-002" \
  -d '{"query": "Find customer C001 and tell me their latest order status."}' | jq
```

Expected: `customer_id: "C001"`, `customer.name: "Alice Johnson"`,
`latest_order.order_id: "O1002"`, `latest_order.status: "DELIVERED"`,
`decision.selected_tools: ["get_customer", "get_latest_order"]`,
`correlation_id: "demo-002"`. (Consumes 1 unit of the shared rate-limit
budget via `customer-agent`'s call to `/api/customers/C001`.)

## 3. Unknown customer (`C999`)

Direct REST:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9080/api/customers/C999 \
  -H "apikey: partilon-api-key-2026"
# 404
```

Through the agentic API:
```bash
curl -s -X POST http://localhost:8010/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Find customer C999"}' | jq
```

Expected: `404`, body `{"detail": {"error": "CUSTOMER_NOT_FOUND", "message": "Customer C999 was not found"}}`.
(Note: `C999` matches the `\bC\d{3}\b` ID pattern, so this exercises the
"valid format, nonexistent record" path, not the "couldn't parse an ID"
path — see scenario 9's sibling case in [docs/api.md](api.md) for the
`CUSTOMER_ID_NOT_FOUND` case, e.g. a query with no `C###` token at all.)

## 4. Order via agent delegation (order only, no customer lookup)

This is the assignment's own A2A example scenario — a query with no
customer ID at all, identified purely by order ID. The coordinator
discovers and delegates to `order-agent` only; `customer-agent` is never
contacted, so this does **not** touch the rate-limited customer route:

```bash
curl -s -X POST http://localhost:8010/agent/query \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: demo-004" \
  -d '{"query": "What is the status of order O1001?"}' | jq
```

Expected: `customer_id: null`, `order_id: "O1001"`, `customer: null`,
`order.status: "SHIPPED"`, `decision.selected_tools: ["get_order"]`,
`a2a` contains only `order_agent` (no `customer_agent` key),
`a2a.delegated_tasks` has exactly one entry. This is
`Coordinator → Order Agent → A2A delegation → Order Service → Order DB`
— covered by `test_coordinator_order_by_id` in `tests/test_agents.py`.

A second order-only variant — a query that mentions "order" *and*
includes a customer ID, but not the word "customer" — takes the
customer-centric code path instead, using `get_latest_order` rather than
`get_order`:

```bash
curl -s -X POST http://localhost:8010/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the latest order for C001?"}' | jq
```

Expected: `customer` is `null`, `latest_order.order_id: "O1002"`,
`decision.selected_tools: ["get_latest_order"]`. (Note the different
field name — `latest_order`, not `order` — and that this path is only
reached because a customer ID is present; see
[docs/api.md](api.md) for how the two order-only paths differ.)

To see the underlying A2A call directly (bypassing the coordinator
entirely):
```bash
curl -s -X POST http://localhost:8012/a2a/tasks \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: demo-004-raw" \
  -d '{"task_id": "demo-order-1", "action": "get_order", "order_id": "O1001"}' | jq
```

## 5. Multi-agent coordination (both agents, one request)

Same mechanics as scenario 2, examined from the orchestration side —
look at the `decision` and `a2a` fields, which show both agents were
independently discovered and delegated to within a single
`/agent/query` call:

```bash
curl -s -X POST http://localhost:8010/agent/query \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: demo-005" \
  -d '{"query": "Find customer C002 and their latest order status."}' | jq '.decision, .a2a'
```

Expected: `decision.selected_tools` contains both `"get_customer"` and
`"get_latest_order"`; `a2a.customer_agent` and `a2a.order_agent` are both
populated agent cards; `a2a.delegated_tasks` has 2 entries
(`customer-demo-005`, `order-demo-005`). `C002`'s latest order is
`O1003` (`PROCESSING`).

## 6. Backend unavailable (customer-service down)

```bash
docker compose stop customer-service

curl -s -X POST http://localhost:8010/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Find customer C001"}' | jq
# 502 {"detail": {"error": "CUSTOMER_AGENT_ERROR", "message": "Customer Agent request failed"}}

docker compose start customer-service
# wait for health, then (per docs/architecture.md's DNS-caching note)
# clear any stale APISIX worker DNS state:
docker compose exec -T apisix apisix reload
```

`customer-agent` itself stayed up and reachable — it's `customer-service`
behind the gateway that was down, which is why the error is
`CUSTOMER_AGENT_ERROR` (agent reachable, its own downstream call failed)
and not `CUSTOMER_AGENT_UNAVAILABLE`. See
[docs/architecture.md → APISIX worker DNS caching](architecture.md#apisix-worker-dns-caching-operational-consideration)
for why the `apisix reload` step matters for a clean recovery.

## 7. Agent unavailable (customer-agent down)

```bash
docker compose stop customer-agent

curl -s -X POST http://localhost:8010/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Find customer C001"}' | jq
# 502 {"detail": {"error": "CUSTOMER_AGENT_UNAVAILABLE", "message": "Customer Agent is unavailable"}}

docker compose start customer-agent
```

Here the coordinator's *discovery* call
(`GET customer-agent/.well-known/agent.json`) fails outright (connection
refused) — a different failure point from scenario 6, hence the
different error code.

## 8. Rate limiting

Run this on its own, with at least 10 seconds of quiet beforehand on the
customer route (so the budget is fresh):

```bash
for i in 1 2 3 4 5 6; do
  curl -s -o /dev/null -w "request $i -> %{http_code}\n" \
    http://localhost:9080/api/customers/C001 \
    -H "apikey: partilon-api-key-2026"
done
```

Expected: requests 1–5 return `200`, request 6 (within the same 10s
window) returns `429`. Wait at least 10 seconds before any later
scenario that also uses `/api/customers/{id}` (scenarios 1, 2, 3, 5, 10),
or you may see an unexpected `429` there instead of the documented
status.

## 9. Unauthorized request

```bash
curl -s -X GET http://localhost:9080/api/customers/C001 | jq
# 401 {"message": "Missing API key in request"}
```

This is rejected by APISIX's `key-auth` plugin before the request
reaches `customer-service` and before it counts against the rate-limit
budget (verified by `test_customer_api_requires_api_key`).

## 10. End-to-end correlation trace

```bash
curl -s -X POST http://localhost:8010/agent/query \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: demo-trace-xyz" \
  -d '{"query": "Find customer C001 and their latest order."}' | jq '.correlation_id'
# "demo-trace-xyz"
```

Then find that ID in every hop's logs:

```bash
docker compose logs coordinator-agent customer-agent order-agent customer-service order-service \
  | grep demo-trace-xyz
```

Expected: a line from `coordinator-agent` (`agent request` /
`agent completed`), from `customer-agent` and `order-agent` (`A2A task
received` / `A2A task completed`), and from `customer-service` and
`order-service` (`request completed`) — all carrying
`correlation_id=demo-trace-xyz`. This is log-based tracing only (plain
text, `docker compose logs`); there is no tracing UI or stored trace
store — see
[README → Correlation ID / observability](../README.md#correlation-id--observability)
for the gaps (no header echo from the agents, no generated ID if one is
omitted).
