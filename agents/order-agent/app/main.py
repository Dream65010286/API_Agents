from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import httpx
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("order-agent")

app = FastAPI(
    title="Partilon Order Agent",
    version="1.0.0",
    description="A2A Order Agent for order information",
)

APISIX_URL = os.getenv("APISIX_URL", "http://apisix:9080")
API_KEY = os.getenv("API_KEY", "partilon-api-key-2026")


class A2ATask(BaseModel):
    task_id: str
    action: str
    customer_id: str | None = None
    order_id: str | None = None


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/.well-known/agent.json")
async def agent_card():
    return {
        "name": "order-agent",
        "description": "Provides order information",
        "capabilities": [
            {
                "name": "get_order",
                "description": "Retrieve an order by order ID",
            },
            {
                "name": "get_latest_order",
                "description": "Retrieve the latest order for a customer",
            },
        ],
        "protocol": "http-json-a2a",
    }


@app.post("/a2a/tasks")
async def execute_task(
    task: A2ATask,
    request: Request,
):
    correlation_id = request.headers.get(
        "X-Correlation-ID",
        "a2a-generated",
    )

    logger.info(
        "A2A task received | task_id=%s | action=%s | customer_id=%s | order_id=%s | correlation_id=%s",
        task.task_id,
        task.action,
        task.customer_id,
        task.order_id,
        correlation_id,
    )

    if task.action == "get_order":
        if not task.order_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "ORDER_ID_REQUIRED",
                    "message": "order_id is required for get_order",
                },
            )

        path = f"/api/orders/{task.order_id}"

    elif task.action == "get_latest_order":
        if not task.customer_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "CUSTOMER_ID_REQUIRED",
                    "message": "customer_id is required for get_latest_order",
                },
            )

        path = f"/api/customers/{task.customer_id}/orders"

    else:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "UNSUPPORTED_ACTION",
                "message": f"Order Agent does not support action '{task.action}'",
            },
        )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{APISIX_URL}{path}",
                headers={
                    "apikey": API_KEY,
                    "X-Correlation-ID": correlation_id,
                },
            )

        logger.info(
            "Order API response | status=%s | task_id=%s | correlation_id=%s",
            response.status_code,
            task.task_id,
            correlation_id,
        )

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail={
                "error": "ORDER_API_TIMEOUT",
                "message": "Order API request timed out",
            },
        )

    except httpx.RequestError as exc:
        logger.error(
            "Order API request error | error=%s | task_id=%s | correlation_id=%s",
            str(exc),
            task.task_id,
            correlation_id,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "ORDER_API_UNAVAILABLE",
                "message": "Order API is unavailable",
            },
        )

    if response.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "ORDER_NOT_FOUND",
                "message": "Order data was not found",
            },
        )

    if response.status_code == 429:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "ORDER_API_RATE_LIMITED",
                "message": "Order API rate limit was exceeded",
            },
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "ORDER_API_ERROR",
                "message": "Order API request failed",
            },
        )

    data = response.json()

    if task.action == "get_latest_order":
        result = data[0] if data else None

        if result is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "LATEST_ORDER_NOT_FOUND",
                    "message": (
                        f"No orders were found for customer {task.customer_id}"
                    ),
                },
            )
    else:
        result = data

    logger.info(
        "A2A task completed | task_id=%s | action=%s | correlation_id=%s",
        task.task_id,
        task.action,
        correlation_id,
    )

    return {
        "task_id": task.task_id,
        "status": "completed",
        "agent": "order-agent",
        "action": task.action,
        "result": result,
        "correlation_id": correlation_id,
    }
