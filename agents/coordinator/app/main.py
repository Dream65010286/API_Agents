from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import httpx
import os
import re
import logging


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("coordinator-agent")


app = FastAPI(
    title="Partilon Coordinator Agent",
    version="1.0.0",
    description="Deterministic Agentic API for customer and order orchestration",
)


APISIX_URL = os.getenv("APISIX_URL", "http://apisix:9080")
API_KEY = os.getenv("API_KEY", "partilon-api-key-2026")
CUSTOMER_AGENT_URL = os.getenv(
    "CUSTOMER_AGENT_URL",
    "http://customer-agent:8000",
)

ORDER_AGENT_URL = os.getenv(
    "ORDER_AGENT_URL",
    "http://order-agent:8000",
)

class AgentRequest(BaseModel):
    query: str


def extract_customer_id(query: str) -> str | None:
    match = re.search(r"\bC\d{3}\b", query.upper())

    if match:
        return match.group(0)

    return None


def extract_order_id(query: str) -> str | None:
    match = re.search(r"\bO\d{4}\b", query.upper())

    if match:
        return match.group(0)

    return None


async def call_managed_api(
    client: httpx.AsyncClient,
    path: str,
    correlation_id: str,
):
    try:
        response = await client.get(
            f"{APISIX_URL}{path}",
            headers={
                "apikey": API_KEY,
                "X-Correlation-ID": correlation_id,
            },
        )

        logger.info(
            "managed API call | path=%s | status=%s | correlation_id=%s",
            path,
            response.status_code,
            correlation_id,
        )

        if response.status_code >= 400:
            return None, response.status_code

        return response.json(), response.status_code

    except httpx.TimeoutException:
        logger.error(
            "managed API timeout | path=%s | correlation_id=%s",
            path,
            correlation_id,
        )
        return None, 504

    except httpx.RequestError as exc:
        logger.error(
            "managed API request error | path=%s | error=%s | correlation_id=%s",
            path,
            str(exc),
            correlation_id,
        )
        return None, 502

async def discover_agent(
    client: httpx.AsyncClient,
    agent_url: str,
    correlation_id: str,
):
    try:
        response = await client.get(
            f"{agent_url}/.well-known/agent.json",
            headers={
                "X-Correlation-ID": correlation_id,
            },
        )

        logger.info(
            "agent discovery | url=%s | status=%s | correlation_id=%s",
            agent_url,
            response.status_code,
            correlation_id,
        )

        if response.status_code >= 400:
            return None, response.status_code

        return response.json(), response.status_code

    except httpx.TimeoutException:
        logger.error(
            "agent discovery timeout | url=%s | correlation_id=%s",
            agent_url,
            correlation_id,
        )
        return None, 504

    except httpx.RequestError as exc:
        logger.error(
            "agent discovery error | url=%s | error=%s | correlation_id=%s",
            agent_url,
            str(exc),
            correlation_id,
        )
        return None, 502


async def call_a2a_task(
    client: httpx.AsyncClient,
    agent_url: str,
    task: dict,
    correlation_id: str,
):
    try:
        response = await client.post(
            f"{agent_url}/a2a/tasks",
            json=task,
            headers={
                "Content-Type": "application/json",
                "X-Correlation-ID": correlation_id,
            },
        )

        logger.info(
            "A2A delegation | agent=%s | task_id=%s | status=%s | correlation_id=%s",
            agent_url,
            task["task_id"],
            response.status_code,
            correlation_id,
        )

        if response.status_code >= 400:
            return None, response.status_code

        return response.json(), response.status_code

    except httpx.TimeoutException:
        logger.error(
            "A2A delegation timeout | agent=%s | task_id=%s | correlation_id=%s",
            agent_url,
            task["task_id"],
            correlation_id,
        )
        return None, 504

    except httpx.RequestError as exc:
        logger.error(
            "A2A delegation error | agent=%s | task_id=%s | error=%s | correlation_id=%s",
            agent_url,
            task["task_id"],
            str(exc),
            correlation_id,
        )
        return None, 502

@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/agent/query")
async def agent_query(
    request: Request,
    agent_request: AgentRequest,
):
    correlation_id = request.headers.get(
        "X-Correlation-ID",
        "agent-generated",
    )

    logger.info(
        "agent request | query=%s | correlation_id=%s",
        agent_request.query,
        correlation_id,
    )

    customer_id = extract_customer_id(agent_request.query)
    order_id = extract_order_id(agent_request.query)

    if customer_id is None and order_id is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "CUSTOMER_ID_NOT_FOUND",
                "message": (
                    "Could not identify a customer ID such as C001 "
                    "or an order ID such as O1001"
                ),
            },
        )

    # ---------------------------------------------------------------
    # Order-only lookup (no customer ID in the query): delegate a
    # single get_order task directly to the Order Agent.
    # Coordinator -> Order Agent -> A2A -> Order Service -> Order DB.
    # ---------------------------------------------------------------
    if customer_id is None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            order_agent_card, status = await discover_agent(
                client,
                ORDER_AGENT_URL,
                correlation_id,
            )

            if status != 200 or order_agent_card is None:
                raise HTTPException(
                    status_code=502 if status not in (504,) else 504,
                    detail={
                        "error": "ORDER_AGENT_UNAVAILABLE",
                        "message": "Order Agent is unavailable",
                    },
                )

            order_task = {
                "task_id": f"order-{correlation_id}",
                "action": "get_order",
                "order_id": order_id,
            }

            order_result, status = await call_a2a_task(
                client,
                ORDER_AGENT_URL,
                order_task,
                correlation_id,
            )

            if status == 404:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "ORDER_NOT_FOUND",
                        "message": f"Order {order_id} was not found",
                    },
                )

            if status == 429:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "ORDER_API_RATE_LIMITED",
                        "message": "Order API rate limit was exceeded",
                    },
                )

            if status == 504:
                raise HTTPException(
                    status_code=504,
                    detail={
                        "error": "ORDER_AGENT_TIMEOUT",
                        "message": "Order Agent request timed out",
                    },
                )

            if order_result is None:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "ORDER_AGENT_ERROR",
                        "message": "Order Agent request failed",
                    },
                )

            order = order_result["result"]

        logger.info(
            "agent completed | order_id=%s | tools=%s | delegated_tasks=%s | correlation_id=%s",
            order_id,
            ["get_order"],
            [order_task["task_id"]],
            correlation_id,
        )

        return {
            "agent": "coordinator",
            "customer_id": None,
            "order_id": order_id,
            "decision": {
                "selected_tools": ["get_order"],
                "reason": (
                    "The Coordinator discovered the Order Agent and "
                    "delegated a get_order task using the A2A protocol."
                ),
            },
            "a2a": {
                "order_agent": order_agent_card,
                "delegated_tasks": [order_task["task_id"]],
            },
            "customer": None,
            "order": order,
            "correlation_id": correlation_id,
        }

    query_lower = agent_request.query.lower()

    needs_customer = "customer" in query_lower
    needs_order = "order" in query_lower

    if not needs_customer and not needs_order:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "UNSUPPORTED_REQUEST",
                "message": (
                    "The Agent could not determine which capability is required"
                ),
            },
        )

    async with httpx.AsyncClient(timeout=5.0) as client:

        customer = None
        latest_order = None
        selected_tools = []
        delegated_tasks = []

        # ---------------------------------------------------------
        # Discover Customer Agent
        # ---------------------------------------------------------
        customer_agent_card = None

        if needs_customer:
            customer_agent_card, status = await discover_agent(
                client,
                CUSTOMER_AGENT_URL,
                correlation_id,
            )

            if status != 200 or customer_agent_card is None:
                raise HTTPException(
                    status_code=502 if status not in (504,) else 504,
                    detail={
                        "error": "CUSTOMER_AGENT_UNAVAILABLE",
                        "message": "Customer Agent is unavailable",
                    },
                )

            selected_tools.append("get_customer")

            customer_task = {
                "task_id": f"customer-{correlation_id}",
                "action": "get_customer",
                "customer_id": customer_id,
            }

            customer_result, status = await call_a2a_task(
                client,
                CUSTOMER_AGENT_URL,
                customer_task,
                correlation_id,
            )

            delegated_tasks.append(customer_task["task_id"])

            if status == 404:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "CUSTOMER_NOT_FOUND",
                        "message": f"Customer {customer_id} was not found",
                    },
                )

            if status == 429:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "CUSTOMER_API_RATE_LIMITED",
                        "message": "Customer API rate limit was exceeded",
                    },
                )

            if status == 504:
                raise HTTPException(
                    status_code=504,
                    detail={
                        "error": "CUSTOMER_AGENT_TIMEOUT",
                        "message": "Customer Agent request timed out",
                    },
                )

            if customer_result is None:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "CUSTOMER_AGENT_ERROR",
                        "message": "Customer Agent request failed",
                    },
                )

            customer = customer_result["result"]

        # ---------------------------------------------------------
        # Discover Order Agent
        # ---------------------------------------------------------
        order_agent_card = None

        if needs_order:
            order_agent_card, status = await discover_agent(
                client,
                ORDER_AGENT_URL,
                correlation_id,
            )

            if status != 200 or order_agent_card is None:
                raise HTTPException(
                    status_code=502 if status not in (504,) else 504,
                    detail={
                        "error": "ORDER_AGENT_UNAVAILABLE",
                        "message": "Order Agent is unavailable",
                    },
                )

            selected_tools.append("get_latest_order")

            order_task = {
                "task_id": f"order-{correlation_id}",
                "action": "get_latest_order",
                "customer_id": customer_id,
            }

            order_result, status = await call_a2a_task(
                client,
                ORDER_AGENT_URL,
                order_task,
                correlation_id,
            )

            delegated_tasks.append(order_task["task_id"])

            if status == 404:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "LATEST_ORDER_NOT_FOUND",
                        "message": (
                            f"No order data was found for customer {customer_id}"
                        ),
                    },
                )

            if status == 429:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "ORDER_API_RATE_LIMITED",
                        "message": "Order API rate limit was exceeded",
                    },
                )

            if status == 504:
                raise HTTPException(
                    status_code=504,
                    detail={
                        "error": "ORDER_AGENT_TIMEOUT",
                        "message": "Order Agent request timed out",
                    },
                )

            if order_result is None:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "ORDER_AGENT_ERROR",
                        "message": "Order Agent request failed",
                    },
                )

            latest_order = order_result["result"]

    logger.info(
        "agent completed | customer_id=%s | tools=%s | delegated_tasks=%s | correlation_id=%s",
        customer_id,
        selected_tools,
        delegated_tasks,
        correlation_id,
    )

    return {
        "agent": "coordinator",
        "customer_id": customer_id,
        "decision": {
            "selected_tools": selected_tools,
            "reason": (
                "The Coordinator discovered the required agents "
                "and delegated tasks using the A2A protocol."
            ),
        },
        "a2a": {
            "customer_agent": customer_agent_card,
            "order_agent": order_agent_card,
            "delegated_tasks": delegated_tasks,
        },
        "customer": customer,
        "latest_order": latest_order,
        "correlation_id": correlation_id,
    }

           
       
