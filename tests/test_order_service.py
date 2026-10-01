import httpx


BASE_URL = "http://127.0.0.1:8002"


def test_order_health():
    response = httpx.get(f"{BASE_URL}/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_get_existing_order():
    response = httpx.get(f"{BASE_URL}/orders/O1002")

    assert response.status_code == 200

    data = response.json()

    assert data["order_id"] == "O1002"
    assert data["customer_id"] == "C001"
    assert data["status"] == "DELIVERED"
    assert data["total_amount"] == 799.0


def test_get_unknown_order():
    response = httpx.get(f"{BASE_URL}/orders/O9999")

    assert response.status_code == 404

    data = response.json()

    assert data["detail"]["error"] == "ORDER_NOT_FOUND"
    assert "O9999" in data["detail"]["message"]


def test_get_customer_orders():
    response = httpx.get(f"{BASE_URL}/customers/C001/orders")

    assert response.status_code == 200

    data = response.json()

    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["order_id"] == "O1002"
    assert data[0]["customer_id"] == "C001"


def test_get_customer_orders_without_orders():
    response = httpx.get(f"{BASE_URL}/customers/C999/orders")

    assert response.status_code == 200
    assert response.json() == []


def test_invalid_order_id_is_rejected_with_400():
    response = httpx.get(f"{BASE_URL}/orders/not-an-order")

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "INVALID_ORDER_ID"


def test_invalid_customer_id_for_orders_is_rejected_with_400():
    response = httpx.get(f"{BASE_URL}/customers/xyz/orders")

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "INVALID_CUSTOMER_ID"
