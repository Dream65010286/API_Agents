import httpx


COORDINATOR_URL = "http://127.0.0.1:8010"
CUSTOMER_AGENT_URL = "http://127.0.0.1:8011"
ORDER_AGENT_URL = "http://127.0.0.1:8012"

API_KEY = "partilon-api-key-2026"


def test_customer_agent_discovery():
    response = httpx.get(
        f"{CUSTOMER_AGENT_URL}/.well-known/agent.json"
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
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "completed"
    assert data["action"] == "get_customer"
    assert data["result"]["customer_id"] == "C001"
    assert data["result"]["name"] == "Alice Johnson"


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
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "completed"
    assert data["action"] == "get_latest_order"
    assert data["result"]["order_id"] == "O1002"
    assert data["result"]["status"] == "DELIVERED"


def test_coordinator_customer_latest_order():
    response = httpx.post(
        f"{COORDINATOR_URL}/agent/query",
        headers={
            "Content-Type": "application/json",
            "X-Correlation-ID": "test-e2e-001",
        },
        json={
            "query": "Find customer C001 and tell me their latest order status."
        },
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
