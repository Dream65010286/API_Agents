import httpx
import pytest


BASE_URL = "http://127.0.0.1:9080"
API_KEY = "partilon-api-key-2026"


def test_customer_api_requires_api_key():
    response = httpx.get(f"{BASE_URL}/api/customers/C001")

    assert response.status_code == 401
    assert response.json()["message"] == "Missing API key in request"


@pytest.mark.customer_api_requests(1)
def test_customer_api_with_valid_api_key():
    response = httpx.get(
        f"{BASE_URL}/api/customers/C001",
        headers={"apikey": API_KEY},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["customer_id"] == "C001"


# The marker waits until no earlier request (from any test, including the
# agent tests) can still count against the 5 requests / 10 seconds window.
@pytest.mark.customer_api_requests(5, exhausts_budget=True)
def test_customer_api_rate_limit():
    headers = {"apikey": API_KEY}

    responses = []

    for _ in range(5):
        response = httpx.get(
            f"{BASE_URL}/api/customers/C001",
            headers=headers,
        )
        responses.append(response)

    assert [response.status_code for response in responses] == [
        200,
        200,
        200,
        200,
        200,
    ]

    response = httpx.get(
        f"{BASE_URL}/api/customers/C001",
        headers=headers,
    )

    assert response.status_code == 429
