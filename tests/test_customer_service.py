import httpx


BASE_URL = "http://127.0.0.1:8001"


def test_customer_health():
    response = httpx.get(f"{BASE_URL}/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_get_existing_customer():
    response = httpx.get(f"{BASE_URL}/customers/C001")

    assert response.status_code == 200

    data = response.json()

    assert data["customer_id"] == "C001"
    assert data["name"] == "Alice Johnson"
    assert data["email"] == "alice@example.com"


def test_get_unknown_customer():
    response = httpx.get(f"{BASE_URL}/customers/C999")

    assert response.status_code == 404

    data = response.json()

    assert data["detail"]["error"] == "CUSTOMER_NOT_FOUND"
    assert "C999" in data["detail"]["message"]
