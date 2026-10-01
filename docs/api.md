# API Reference

All request/response shapes below are taken directly from the
implementation (`services/*/app/main.py`, `agents/*/app/main.py`) and
cross-checked against `tests/`. Timestamps in examples use the seed data
from `database/*/init.sql`.

Error bodies from the FastAPI services/agents always have the shape:

```json
{"detail": {"error": "SOME_CODE", "message": "human-readable text"}}
```

Error bodies from **APISIX itself** (e.g. a missing/invalid API key, or a
rate-limit rejection) are **not** in that shape — they're APISIX's own
flat JSON, e.g. `{"message": "Missing API key in request"}`, or, for a
path with no matching route at all, `{"error_msg": "404 Route Not
Found"}` (verified directly against a freshly provisioned stack — see
[docs/architecture.md](architecture.md#a-real-world-consequence-a-missing-route-looks-like-a-404-from-the-data-not-the-gateway)).
Because that's a plain `404`, and the agents don't distinguish it from a
real "not found," a gateway with no routes provisioned yet will make
every customer/order look like it doesn't exist, rather than surfacing a
gateway-configuration error.

---

## customer-service (direct: `http://localhost:8001`)

No authentication. Not rate limited. Every response carries
`X-Correlation-ID` (echoing the request header, or the literal string
`"missing"` if the request didn't send one).

### `GET /health`

```json
{"status": "healthy"}
```

### `GET /customers/{customer_id}`

**200**
```json
{"customer_id": "C001", "name": "Alice Johnson", "email": "alice@example.com"}
```

**404** (`customer_id` not in `customers` table)
```json
{"detail": {"error": "CUSTOMER_NOT_FOUND", "message": "Customer C999 was not found"}}
```

---

## order-service (direct: `http://localhost:8002`)

No authentication. Not rate limited. Same `X-Correlation-ID` echo
behavior as `customer-service` (note: `order-service`'s middleware is
registered twice in code, so each request is logged twice server-side —
harmless, see [README → Known limitations](../README.md#known-limitations)).

### `GET /health`

```json
{"status": "healthy"}
```

### `GET /orders/{order_id}`

**200**
```json
{"order_id": "O1002", "customer_id": "C001", "order_date": "2026-09-28T14:15:00", "status": "DELIVERED", "total_amount": 799.0}
```

**404**
```json
{"detail": {"error": "ORDER_NOT_FOUND", "message": "Order O9999 was not found"}}
```

### `GET /customers/{customer_id}/orders`

Returns every order for that customer, newest first (`ORDER BY order_date DESC`).

**200** (has orders)
```json
[
  {"order_id": "O1002", "customer_id": "C001", "order_date": "2026-09-28T14:15:00", "status": "DELIVERED", "total_amount": 799.0},
  {"order_id": "O1001", "customer_id": "C001", "order_date": "2026-09-25T10:30:00", "status": "SHIPPED", "total_amount": 1299.0}
]
```

**200** (no orders, e.g. `C999`, or any customer that exists but has
placed none) — an **empty list**, not a 404:
```json
[]
```

---

## API Gateway (APISIX: `http://localhost:9080`)

Both routes below require header `apikey: partilon-api-key-2026`.
See [docs/architecture.md](architecture.md) for how these routes are
provisioned (not from a file in this repo) and their DNS-caching
behavior on backend restarts.

### `GET /api/customers/{customer_id}` → proxied to customer-service

- Rate limited: **5 requests / 10 seconds**, shared across every caller
  using the one API key.
- Missing `apikey` → **401**, APISIX body:
  ```json
  {"message": "Missing API key in request"}
  ```
- Budget exceeded → **429** (exact APISIX body not asserted by the test
  suite beyond the status code).
- Otherwise identical response to `GET /customers/{customer_id}` on
  `customer-service` directly.

### `GET /api/orders/{order_id}` and `GET /api/customers/{customer_id}/orders` → proxied to order-service

- **Not** rate limited.
- Same `apikey` requirement as above.
- Otherwise identical responses to the equivalent `order-service` routes.

---

## Agent cards (discovery)

### `GET /.well-known/agent.json` — customer-agent (`http://localhost:8011`)

```json
{
  "name": "customer-agent",
  "description": "Provides customer information",
  "capabilities": [
    {"name": "get_customer", "description": "Retrieve customer information by customer ID"}
  ],
  "protocol": "http-json-a2a"
}
```

### `GET /.well-known/agent.json` — order-agent (`http://localhost:8012`)

```json
{
  "name": "order-agent",
  "description": "Provides order information",
  "capabilities": [
    {"name": "get_order", "description": "Retrieve an order by order ID"},
    {"name": "get_latest_order", "description": "Retrieve the latest order for a customer"}
  ],
  "protocol": "http-json-a2a"
}
```

`coordinator-agent` has no agent card of its own — see
[docs/a2a.md](a2a.md).

---

## A2A task delegation

See [docs/a2a.md](a2a.md) for the full protocol description. Quick
reference:

### `POST /a2a/tasks` — customer-agent

Request:
```json
{"task_id": "customer-abc-123", "action": "get_customer", "customer_id": "C001"}
```

Success (200):
```json
{
  "task_id": "customer-abc-123",
  "status": "completed",
  "agent": "customer-agent",
  "action": "get_customer",
  "result": {"customer_id": "C001", "name": "Alice Johnson", "email": "alice@example.com"},
  "correlation_id": "abc-123"
}
```

Only `action: "get_customer"` is supported; anything else → `400 UNSUPPORTED_ACTION`.

### `POST /a2a/tasks` — order-agent

Request (`get_latest_order`):
```json
{"task_id": "order-abc-123", "action": "get_latest_order", "customer_id": "C001"}
```

Request (`get_order` — implemented, but not used by the coordinator or
covered by tests):
```json
{"task_id": "order-xyz", "action": "get_order", "order_id": "O1002"}
```

Success (200, `get_latest_order`):
```json
{
  "task_id": "order-abc-123",
  "status": "completed",
  "agent": "order-agent",
  "action": "get_latest_order",
  "result": {"order_id": "O1002", "customer_id": "C001", "order_date": "2026-09-28T14:15:00", "status": "DELIVERED", "total_amount": 799.0},
  "correlation_id": "abc-123"
}
```

`action` must be `get_order` or `get_latest_order`; anything else →
`400 UNSUPPORTED_ACTION`. `get_order` without `order_id`, or
`get_latest_order` without `customer_id` → `400` (`ORDER_ID_REQUIRED` /
`CUSTOMER_ID_REQUIRED`).

---

## Agentic API — coordinator-agent (`http://localhost:8010`)

### `GET /health`

```json
{"status": "healthy"}
```

### `POST /agent/query`

Request:
```json
{"query": "Find customer C001 and tell me their latest order status."}
```

Header `X-Correlation-ID` is optional; if provided, it is threaded
through every downstream discovery/A2A call and echoed back in the
response body's `correlation_id` field (not as a response header — see
[README → Correlation ID](../README.md#correlation-id--observability)).

Success (200) — both "customer" and "order" present in the query:
```json
{
  "agent": "coordinator",
  "customer_id": "C001",
  "decision": {
    "selected_tools": ["get_customer", "get_latest_order"],
    "reason": "The Coordinator discovered the required agents and delegated tasks using the A2A protocol."
  },
  "a2a": {
    "customer_agent": { "...agent card..." },
    "order_agent": { "...agent card..." },
    "delegated_tasks": ["customer-<correlation_id>", "order-<correlation_id>"]
  },
  "customer": {"customer_id": "C001", "name": "Alice Johnson", "email": "alice@example.com"},
  "latest_order": {"order_id": "O1002", "customer_id": "C001", "order_date": "2026-09-28T14:15:00", "status": "DELIVERED", "total_amount": 799.0},
  "correlation_id": "test-e2e-001"
}
```

If a customer ID is present and the query only mentions "customer" (not
"order"), `latest_order` is `null` and only `get_customer` is delegated
— and vice versa if it only mentions "order" (`customer` is `null`, only
`get_latest_order` is delegated). This keyword check only applies **when
a customer ID was found** — see the order-ID-only case below for queries
with no customer ID at all. The planning logic is a plain substring
check on the lower-cased query for the words `"customer"` and `"order"`
— see [README → Agentic API](../README.md#agentic-api).

#### Request (order ID only, no customer ID)

A query with no `\bC\d{3}\b` token but an `\bO\d{4}\b` token (e.g.
`"What is the status of order O1001?"`) takes a different path entirely:
`Coordinator → Order Agent → A2A delegation → Order Service → Order DB`,
with no Customer Agent involved and a different response shape:

```json
{"query": "What is the status of order O1001?"}
```

Success (200):
```json
{
  "agent": "coordinator",
  "customer_id": null,
  "order_id": "O1001",
  "decision": {
    "selected_tools": ["get_order"],
    "reason": "The Coordinator discovered the Order Agent and delegated a get_order task using the A2A protocol."
  },
  "a2a": {
    "order_agent": { "...agent card..." },
    "delegated_tasks": ["order-<correlation_id>"]
  },
  "customer": null,
  "order": {"order_id": "O1001", "customer_id": "C001", "order_date": "2026-09-25T10:30:00", "status": "SHIPPED", "total_amount": 1299.0},
  "correlation_id": "..."
}
```

Note the distinct `"order"` field (not `"latest_order"`, which is
reserved for the customer-centric flow above) and the absence of a
`customer_agent` entry under `a2a` — `customer-agent` is never contacted
for this request. A query containing *both* a customer ID and an order
ID takes the customer-centric flow instead (above), and the order ID in
the text is ignored — see [README → Known limitations](../README.md#known-limitations).

#### Error responses

| HTTP | `error` code | When |
|---|---|---|
| 400 | `CUSTOMER_ID_NOT_FOUND` | Neither a `\bC\d{3}\b` (e.g. `C001`) nor an `\bO\d{4}\b` (e.g. `O1001`) pattern was found in the query — the error code name predates order-ID support and was kept to avoid changing the documented contract; the message text covers both cases |
| 400 | `UNSUPPORTED_REQUEST` | A customer ID was found, but the query contains neither "customer" nor "order" |
| 404 | `CUSTOMER_NOT_FOUND` | customer-agent's A2A call returned 404 |
| 404 | `LATEST_ORDER_NOT_FOUND` | order-agent's `get_latest_order` A2A call returned 404 (customer exists but has no orders, or is unknown) — customer-centric flow only |
| 404 | `ORDER_NOT_FOUND` | order-agent's `get_order` A2A call returned 404 (unknown order ID) — order-ID-only flow only |
| 429 | `CUSTOMER_API_RATE_LIMITED` / `ORDER_API_RATE_LIMITED` | The relevant APISIX route's rate limit was already exceeded |
| 502 | `CUSTOMER_AGENT_UNAVAILABLE` / `ORDER_AGENT_UNAVAILABLE` | Discovery call (`/.well-known/agent.json`) to that agent failed (connection error, non-200, or any non-504 failure) |
| 502 | `CUSTOMER_AGENT_ERROR` / `ORDER_AGENT_ERROR` | The agent was discoverable but its `/a2a/tasks` call failed for a reason other than 404/429/504 (typically: its backend API is unreachable) |
| 504 | `CUSTOMER_AGENT_TIMEOUT` / `ORDER_AGENT_TIMEOUT` | The A2A call to that agent timed out (5s client-side timeout) |
| 504 | (same as above, from discovery) | Discovery call itself timed out |

All of the above are implemented. All rows except the two `*_TIMEOUT`
rows are directly exercised by `tests/test_agents.py`, including the
order-ID-only rows (`test_coordinator_order_by_id`,
`test_coordinator_order_by_id_unknown_order`,
`test_coordinator_no_id_in_query`).
