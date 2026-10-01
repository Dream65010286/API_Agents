import httpx
import pytest


COORDINATOR_URL = "http://127.0.0.1:8010"
CUSTOMER_AGENT_URL = "http://127.0.0.1:8011"
ORDER_AGENT_URL = "http://127.0.0.1:8012"


# ---------------------------------------------------------------------------
# Customer Agent
# ---------------------------------------------------------------------------

def test_customer_agent_discovery():
    response = httpx.get(
        f"{CUSTOMER_AGENT_URL}/.well-known/agent.json",
        timeout=5.0,
    )

    assert response.status_code == 200

    data = response.json()

    assert data["name"] == "customer-agent"
    assert data["protocol"] == "http-json-a2a"

    capabilities = [
        capability["name"]
        for capability in data["capabilities"]
    ]

    assert "get_customer" in capabilities


@pytest.mark.customer_api_requests(1)
def test_customer_agent_a2a_get_customer():
    response = httpx.post(
        f"{CUSTOMER_AGENT_URL}/a2a/tasks",
        headers={
            "Content-Type": "application/json",
            "X-Correlation-ID": "test-a2a-customer-001",
        },
        json={
            "task_id": "test-customer-001",
            "action": "get_customer",
            "customer_id": "C001",
        },
        timeout=10.0,
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "completed"
    assert data["action"] == "get_customer"
    assert data["result"]["customer_id"] == "C001"
    assert data["result"]["name"] == "Alice Johnson"


# ---------------------------------------------------------------------------
# Order Agent
# ---------------------------------------------------------------------------

def test_order_agent_a2a_get_latest_order():
    response = httpx.post(
        f"{ORDER_AGENT_URL}/a2a/tasks",
        headers={
            "Content-Type": "application/json",
            "X-Correlation-ID": "test-a2a-order-001",
        },
        json={
            "task_id": "test-order-001",
            "action": "get_latest_order",
            "customer_id": "C001",
        },
        timeout=10.0,
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "completed"
    assert data["action"] == "get_latest_order"
    assert data["result"]["order_id"] == "O1002"
    assert data["result"]["status"] == "DELIVERED"


# ---------------------------------------------------------------------------
# Coordinator E2E
# ---------------------------------------------------------------------------

@pytest.mark.customer_api_requests(1)
def test_coordinator_customer_latest_order():
    response = httpx.post(
        f"{COORDINATOR_URL}/agent/query",
        headers={
            "Content-Type": "application/json",
            "X-Correlation-ID": "test-e2e-001",
        },
        json={
            "query": (
                "Find customer C001 and tell me "
                "their latest order status."
            )
        },
        timeout=10.0,
    )

    assert response.status_code == 200

    data = response.json()

    assert data["customer_id"] == "C001"

    assert data["customer"]["name"] == "Alice Johnson"

    assert data["latest_order"]["order_id"] == "O1002"
    assert data["latest_order"]["status"] == "DELIVERED"

    assert "get_customer" in data["decision"]["selected_tools"]
    assert "get_latest_order" in data["decision"]["selected_tools"]

    assert len(data["a2a"]["delegated_tasks"]) == 2

    assert data["correlation_id"] == "test-e2e-001"


# ---------------------------------------------------------------------------
# Coordinator: order-only lookup (no customer ID in the query)
#
# "What is the status of order O1001?" -- Coordinator -> Order Agent ->
# A2A delegation -> Order Service -> Order DB, with no Customer Agent
# involved at all.
# ---------------------------------------------------------------------------

def test_coordinator_order_by_id():
    response = httpx.post(
        f"{COORDINATOR_URL}/agent/query",
        headers={
            "Content-Type": "application/json",
            "X-Correlation-ID": "test-order-by-id-001",
        },
        json={
            "query": "What is the status of order O1001?"
        },
        timeout=10.0,
    )

    assert response.status_code == 200

    data = response.json()

    assert data["customer_id"] is None
    assert data["order_id"] == "O1001"

    assert data["customer"] is None
    assert data["order"]["order_id"] == "O1001"
    assert data["order"]["status"] == "SHIPPED"

    assert data["decision"]["selected_tools"] == ["get_order"]

    assert len(data["a2a"]["delegated_tasks"]) == 1

    assert data["correlation_id"] == "test-order-by-id-001"


def test_coordinator_order_by_id_unknown_order():
    response = httpx.post(
        f"{COORDINATOR_URL}/agent/query",
        headers={
            "Content-Type": "application/json",
        },
        json={
            "query": "What is the status of order O9999?"
        },
        timeout=10.0,
    )

    assert response.status_code == 404

    data = response.json()

    assert data["detail"]["error"] == "ORDER_NOT_FOUND"
    assert "O9999" in data["detail"]["message"]


def test_coordinator_no_id_in_query():
    response = httpx.post(
        f"{COORDINATOR_URL}/agent/query",
        headers={
            "Content-Type": "application/json",
        },
        json={
            "query": "Hello there, how are you?"
        },
        timeout=10.0,
    )

    assert response.status_code == 400

    data = response.json()

    assert data["detail"]["error"] == "CUSTOMER_ID_NOT_FOUND"


# ---------------------------------------------------------------------------
# Failure handling: Customer Agent unavailable
# ---------------------------------------------------------------------------

def test_coordinator_customer_agent_unavailable(stopped_service):
    with stopped_service("customer-agent"):
        response = httpx.post(
            f"{COORDINATOR_URL}/agent/query",
            headers={
                "Content-Type": "application/json",
                "X-Correlation-ID": (
                    "test-failure-customer-agent-001"
                ),
            },
            json={
                "query": "Find customer C001"
            },
            timeout=10.0,
        )

        assert response.status_code == 502

        data = response.json()

        assert (
            data["detail"]["error"]
            == "CUSTOMER_AGENT_UNAVAILABLE"
        )


# ---------------------------------------------------------------------------
# Failure handling: Order Agent unavailable
# ---------------------------------------------------------------------------

# The query also needs the customer, so the Customer Agent calls the
# rate-limited customer-api route before the Order Agent is contacted.
@pytest.mark.customer_api_requests(1)
def test_coordinator_order_agent_unavailable(stopped_service):
    with stopped_service("order-agent"):
        response = httpx.post(
            f"{COORDINATOR_URL}/agent/query",
            headers={
                "Content-Type": "application/json",
                "X-Correlation-ID": (
                    "test-failure-order-agent-001"
                ),
            },
            json={
                "query": (
                    "Find customer C001 and tell me "
                    "their latest order status."
                )
            },
            timeout=10.0,
        )

        assert response.status_code == 502

        data = response.json()

        assert (
            data["detail"]["error"]
            == "ORDER_AGENT_UNAVAILABLE"
        )


# ---------------------------------------------------------------------------
# Failure handling: Customer Backend unavailable
# ---------------------------------------------------------------------------

# The failed request still passes limit-count before APISIX finds no upstream.
@pytest.mark.customer_api_requests(1)
def test_coordinator_customer_backend_unavailable(stopped_service):
    with stopped_service("customer-service"):
        response = httpx.post(
            f"{COORDINATOR_URL}/agent/query",
            headers={
                "Content-Type": "application/json",
                "X-Correlation-ID": (
                    "test-failure-customer-backend-001"
                ),
            },
            json={
                "query": "Find customer C001"
            },
            timeout=10.0,
        )

        assert response.status_code == 502

        data = response.json()

        assert (
            data["detail"]["error"]
            == "CUSTOMER_AGENT_ERROR"
        )
