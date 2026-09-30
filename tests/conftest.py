"""
Shared fixtures for the Docker Compose integration test suite.

Two pieces of shared state couple the tests to each other:

1. The APISIX ``customer-api`` route is rate limited per consumer
   (limit-count: 5 requests / 10 seconds, key=consumer_name).  Every test
   that reaches that route -- directly, or indirectly through the Customer
   Agent / Coordinator -- draws from the same budget.  ``customer_api_budget``
   tracks that budget so no test starts with an exhausted window.

2. Each APISIX nginx worker keeps its own DNS cache.  Once a worker has
   resolved ``customer-service`` while the container was stopped (NXDOMAIN),
   it keeps answering 503 "no valid upstream node" long after the service is
   back.  Workers that cached the old IP instead hang on a dead address while
   the service is down.  ``reset_gateway_workers`` reloads APISIX (fresh
   workers, same config and same rate-limit counters) so the outage and the
   recovery are both deterministic.
"""

import contextlib
import subprocess
import time
from pathlib import Path

import httpx
import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent

APISIX_URL = "http://127.0.0.1:9080"
CUSTOMER_SERVICE_URL = "http://127.0.0.1:8001"
ORDER_SERVICE_URL = "http://127.0.0.1:8002"
COORDINATOR_URL = "http://127.0.0.1:8010"
CUSTOMER_AGENT_URL = "http://127.0.0.1:8011"
ORDER_AGENT_URL = "http://127.0.0.1:8012"

API_KEY = "partilon-api-key-2026"

# Must match the limit-count plugin on the APISIX "customer-api" route.
CUSTOMER_API_RATE_LIMIT_COUNT = 5
CUSTOMER_API_RATE_LIMIT_WINDOW = 10

# Services that the failure-injection tests stop and start again.
FAILURE_INJECTED_SERVICES = (
    "customer-service",
    "customer-agent",
    "order-agent",
)

APP_SERVICES = (
    "customer-service",
    "order-service",
    "customer-agent",
    "order-agent",
    "coordinator-agent",
    "apisix",
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "customer_api_requests(n, exhausts_budget=False): the test sends n "
        "requests through the rate-limited APISIX customer-api route",
    )


# ---------------------------------------------------------------------------
# Readiness helpers
# ---------------------------------------------------------------------------

def compose(*args, check=True):
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


def wait_for_http(
    url,
    timeout=30,
    headers=None,
    expected_status=200,
    consecutive=1,
):
    """
    Wait until an HTTP endpoint returns the expected status code
    ``consecutive`` times in a row.

    Never point this at the rate-limited customer-api route with an API key:
    every attempt would consume the rate-limit budget.
    """

    deadline = time.time() + timeout
    last_status = None
    successes = 0

    while time.time() < deadline:
        try:
            response = httpx.get(url, headers=headers, timeout=2.0)
            last_status = response.status_code
        except httpx.RequestError as exc:
            last_status = type(exc).__name__

        if last_status == expected_status:
            successes += 1
            if successes >= consecutive:
                return
            continue

        successes = 0
        time.sleep(0.5)

    raise AssertionError(
        f"Service did not become ready: {url} "
        f"(expected HTTP {expected_status}, last status={last_status})"
    )


def wait_for_container(service_name, timeout=30):
    """
    Wait until a Docker Compose service has a running container.

    Uses the Compose service name, not the generated container name.
    """

    deadline = time.time() + timeout

    while time.time() < deadline:
        container_id = compose("ps", "-q", service_name, check=False).stdout.strip()

        if container_id:
            inspect = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", container_id],
                capture_output=True,
                text=True,
            )

            if inspect.stdout.strip() == "running":
                return

        time.sleep(0.5)

    raise AssertionError(f"Container did not become ready: {service_name}")


def wait_for_gateway_ready():
    """
    Wait until every APISIX worker has loaded its routes and proxies traffic,
    without touching the customer-api rate-limit budget.

    - customer-api without an API key -> 401: the route is loaded, and
      key-auth rejects the request before limit-count counts it.
    - order-api with the API key -> 200: the gateway proxies to an upstream;
      this route is not rate limited.

    Requests are spread across workers, so require a run of consecutive
    successes rather than a single one.
    """

    wait_for_http(
        f"{APISIX_URL}/api/customers/C001",
        expected_status=401,
        consecutive=10,
    )

    wait_for_http(
        f"{APISIX_URL}/api/orders/O1002",
        headers={"apikey": API_KEY},
        consecutive=10,
    )


def reset_gateway_workers():
    """
    Replace the APISIX nginx workers so no worker holds DNS state
    (a stale IP or a cached NXDOMAIN) for a restarted container.

    Routes, consumers and limit-count counters are unaffected.
    """

    compose("exec", "-T", "apisix", "apisix", "reload")
    wait_for_gateway_ready()


def wait_for_service_ready(service_name):
    if service_name == "customer-service":
        wait_for_http(f"{CUSTOMER_SERVICE_URL}/health")
    elif service_name == "order-service":
        wait_for_http(f"{ORDER_SERVICE_URL}/health")
    elif service_name == "customer-agent":
        wait_for_http(f"{CUSTOMER_AGENT_URL}/.well-known/agent.json")
    elif service_name == "order-agent":
        wait_for_http(f"{ORDER_AGENT_URL}/.well-known/agent.json")
    elif service_name == "coordinator-agent":
        wait_for_http(f"{COORDINATOR_URL}/health")
    elif service_name == "apisix":
        wait_for_gateway_ready()
    else:
        raise ValueError(f"Unknown service: {service_name}")


# ---------------------------------------------------------------------------
# Rate-limit budget
# ---------------------------------------------------------------------------

class CustomerApiBudget:
    """
    Conservative model of the customer-api limit-count budget.

    Once no request has reached the route for a full window (plus a margin),
    every APISIX counter has expired.  From that point, as long as at most
    ``count`` requests are sent in total, no window can exceed the limit,
    however the windows happen to align.
    """

    def __init__(self, count, window, margin=1.0):
        self.count = count
        self.window = window
        self.margin = margin
        # The state left behind by earlier runs is unknown: assume exhausted.
        self.used = count
        self.last_use = time.time()

    def _wait_for_fresh_window(self):
        remaining = self.last_use + self.window + self.margin - time.time()
        if remaining > 0:
            time.sleep(remaining)
        self.used = 0

    def reserve(self, n):
        if self.used + n > self.count:
            self._wait_for_fresh_window()
        self.used += n
        self.last_use = time.time()

    def exhaust(self):
        self.used = self.count
        self.last_use = time.time()

    def touch(self):
        self.last_use = time.time()


@pytest.fixture(scope="session")
def customer_api_budget():
    return CustomerApiBudget(
        CUSTOMER_API_RATE_LIMIT_COUNT,
        CUSTOMER_API_RATE_LIMIT_WINDOW,
    )


@pytest.fixture(autouse=True)
def _reserve_customer_api_budget(request, customer_api_budget):
    marker = request.node.get_closest_marker("customer_api_requests")

    if marker is None:
        yield
        return

    customer_api_budget.reserve(marker.args[0])

    yield

    if marker.kwargs.get("exhausts_budget"):
        customer_api_budget.exhaust()
    else:
        customer_api_budget.touch()


# ---------------------------------------------------------------------------
# Healthy baseline
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def healthy_baseline():
    """
    Bring the stack to a known-healthy state before any test runs,
    including after an interrupted earlier run.
    """

    # A previous run may have been interrupted while a service was stopped.
    compose("start", *FAILURE_INJECTED_SERVICES)

    for service_name in APP_SERVICES:
        wait_for_container(service_name)

    for service_name in APP_SERVICES:
        wait_for_service_ready(service_name)

    # Clear any per-worker DNS state left behind by earlier runs.
    reset_gateway_workers()


# ---------------------------------------------------------------------------
# Failure injection
# ---------------------------------------------------------------------------

@pytest.fixture
def stopped_service(customer_api_budget):
    """
    Context manager that stops a Compose service and, on exit, starts it
    again and waits until it is genuinely ready for the next test.
    """

    @contextlib.contextmanager
    def _stopped_service(service_name):
        if service_name == "customer-service":
            # Workers that already cached the customer-service IP would hang
            # on the dead address instead of failing fast; start from fresh
            # workers so the outage is observed deterministically.
            reset_gateway_workers()

        compose("stop", service_name)

        try:
            yield
        finally:
            compose("start", service_name)
            wait_for_container(service_name)
            wait_for_service_ready(service_name)

            if service_name == "customer-service":
                # Drop the NXDOMAIN answers cached during the outage, then
                # prove the managed API works end to end through APISIX.
                reset_gateway_workers()

                customer_api_budget.reserve(1)
                response = httpx.get(
                    f"{APISIX_URL}/api/customers/C001",
                    headers={"apikey": API_KEY},
                    timeout=5.0,
                )
                customer_api_budget.touch()

                assert response.status_code == 200, (
                    "customer-api did not recover through APISIX: "
                    f"HTTP {response.status_code}"
                )

    return _stopped_service
