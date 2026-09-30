import time

import httpx


BASE_URL = "http://127.0.0.1:9080"
API_KEY = "partilon-api-key-2026"


def test_customer_api_requires_api_key():
    response = httpx.get(f"{BASE_URL}/api/customers/C001")

    assert response.status_code == 401
    assert response.json()["message"] == "Missing API key in request"


def test_customer_api_with_valid_api_key():
    response = httpx.get(
        f"{BASE_URL}/api/customers/C001",
        headers={"apikey": API_KEY},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["customer_id"] == "C001"


def test_customer_api_rate_limit():
    headers = {"apikey": API_KEY}

    # Wait for the existing 10-second rate-limit window to expire.
    time.sleep(11)

    responses = []

    for _ in range(5):
        response = httpx.get(
            f"{BASE_URL}/api/customers/C001",
            headers=headers,
        )
        responses.append(response)

    assert all(response.status_code == 200 for response in responses)

    response = httpx.get(
        f"{BASE_URL}/api/customers/C001",
        headers=headers,
    )

    assert response.status_code == 429
