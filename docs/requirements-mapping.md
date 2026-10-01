# Requirements Mapping

**A note on source material:** the original assessment brief for this
project ("Partilon Technical Assessment") is not stored in this
repository, so this mapping is built from the functional areas visible
in the implementation itself and the areas the project's own naming and
structure point to (a REST API + API Gateway + an agentic/A2A layer on
top). If you are grading this against a specific written brief, use the
table below as a cross-reference against the actual requirement text,
not as a restatement of it.

| # | Requirement area | Status | Evidence | Notes |
|---|---|---|---|---|
| 1 | REST API for core resources (customers, orders) | **Implemented** | `services/customer-service/app/main.py`, `services/order-service/app/main.py`, `tests/test_customer_service.py`, `tests/test_order_service.py` | Read-only (GET only); no create/update/delete endpoints exist. |
| 2 | Database-backed persistence | **Implemented** | `database/customer/init.sql`, `database/order/init.sql`, two separate Postgres containers | Database-per-service; no cross-service SQL. Seed data only (2 customers, 3 orders); no migration tooling beyond the init SQL script. |
| 3 | API Gateway in front of the APIs | **Implemented** | `docker-compose.yml` (`apisix`, `etcd`, `apisix-provision` services), `gateway/apisix/config/config.yaml`, `gateway/apisix/provision/provision.sh`, `tests/test_gateway.py` | Node config plus routes/upstreams/consumer are now both provisioned automatically and reproducibly from a clean clone — see row 13. |
| 4 | Authentication at the gateway | **Implemented** | APISIX `key-auth` plugin (behavior verified by `test_customer_api_requires_api_key`) | Gateway-only. Backend services and agents have no auth of their own — see [README → Known limitations](../README.md#known-limitations). Single shared API key, no per-client identity. |
| 5 | Rate limiting | **Partially implemented** | `test_customer_api_rate_limit` (5 req / 10s, verified) | Applied to the customer route only; the order routes are explicitly not rate limited (verified by the test budget design in `tests/conftest.py`). |
| 6 | Correlation ID / request tracing | **Partially implemented** | Middleware in `services/*/app/main.py`; inline handling in `agents/*/app/main.py` | No server-generated UUID fallback; inconsistent between services (response header) and agents (JSON body only); no centralized tracing backend. See [README → Correlation ID](../README.md#correlation-id--observability). |
| 7 | Agentic API (a single endpoint that orchestrates underlying services) | **Implemented** | `agents/coordinator/app/main.py` (`POST /agent/query`), `tests/test_agents.py::test_coordinator_customer_latest_order`, `tests/test_agents.py::test_coordinator_order_by_id` | Deterministic keyword/regex planner, not an LLM. Handles both customer-centric queries and order-ID-only queries (e.g. "What is the status of order O1001?"). See assumption in README. |
| 8 | Agent discovery | **Implemented** | `GET /.well-known/agent.json` on `customer-agent`/`order-agent`, `tests/test_agents.py::test_customer_agent_discovery` | Static agent cards, no dynamic capability negotiation; coordinator has no card of its own (not discoverable itself). |
| 9 | Agent-to-Agent (A2A) communication | **Implemented, simplified** | `POST /a2a/tasks` on both worker agents, `tests/test_agents.py` | Custom HTTP/JSON protocol inspired by, but not conformant with, the formal A2A spec (no JSON-RPC, no task polling/streaming, no auth). Full comparison in [docs/a2a.md](a2a.md). |
| 10 | Failure handling / resilience | **Implemented** | Timeout/connection-error handling in every agent's outbound call; `tests/test_agents.py` (`test_coordinator_customer_agent_unavailable`, `test_coordinator_order_agent_unavailable`, `test_coordinator_customer_backend_unavailable`) | Covers "agent down" and "backend down" paths with real container stop/start. Timeout paths (504) exist in code but are not exercised by an automated test (hard to simulate a real 5s hang deterministically in CI). |
| 11 | Containerized deployment | **Implemented** | `docker-compose.yml`, one `Dockerfile` per service/agent | Single-host Docker Compose only. No Kubernetes/Helm, no CI/CD pipeline in this repository. |
| 12 | Automated test suite | **Implemented** | `tests/` (4 files, 21 tests) | 21/21 passing reliably against the live Compose stack (no mocks): 18 original (stabilized in a dedicated commit touching only `tests/`) plus 3 added for the order-by-ID path — see [README → Testing](../README.md#testing). |
| 13 | Declarative/reproducible gateway configuration | **Implemented** | `gateway/apisix/provision/provision.sh`, `docker-compose.yml` (`apisix-provision` service) | Routes, upstreams, consumer, and the rate-limit plugin are declared in a committed script and applied automatically and idempotently on every `docker compose up`, including against a fresh `etcd` volume — verified directly (`docker compose down -v` equivalent, then `up`, zero manual steps). See [docs/architecture.md](architecture.md#apisix-provisioning-declarative-script-applied-automatically). |
| 14 | Write operations (create/update/delete) on customers or orders | **Not implemented** | — | Out of scope: both APIs are read-only. |
| 15 | Multi-turn / conversational agent interaction | **Not implemented** | — | `POST /agent/query` is single-turn/stateless; no conversation or session state is kept between calls. |

## Summary

Everything marked **Implemented** or **Partially implemented** above is
real, running code with at least one passing automated test behind it
(exceptions noted inline, e.g. the untested timeout paths). Nothing in
this document describes a feature that exists only as a plan or a
stub — rows that aren't implemented are listed as **Not implemented**
and point to the corresponding limitation/consideration section in the
README rather than being described as done.
