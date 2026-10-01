from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import httpx
import os
import uuid
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("customer-agent")

app = FastAPI(
    title="Partilon Customer Agent",
    version="1.0.0",
    description="A2A Customer Agent for customer information",
)

APISIX_URL = os.getenv("APISIX_URL", "http://apisix:9080")
API_KEY = os.getenv("API_KEY", "partilon-api-key-2026")


class A2ATask(BaseModel):
    task_id: str
    action: str
    customer_id: str


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/.well-known/agent.json")
async def agent_card():
    return {
        "name": "customer-agent",
        "description": "Provides customer information",
        "capabilities": [
            {
                "name": "get_customer",
                "description": "Retrieve customer information by customer ID",
            }
        ],
        "protocol": "http-json-a2a",
    }


@app.post("/a2a/tasks")
async def execute_task(
    task: A2ATask,
    request: Request,
):
    correlation_id = (request.headers.get("X-Correlation-ID") or uuid.uuid4().hex)

    logger.info(
        "A2A task received | task_id=%s | action=%s | customer_id=%s | correlation_id=%s",
        task.task_id,
        task.action,
        task.customer_id,
        correlation_id,
    )

    if task.action != "get_customer":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "UNSUPPORTED_ACTION",
                "message": f"Customer Agent does not support action '{task.action}'",
            },
        )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{APISIX_URL}/api/customers/{task.customer_id}",
                headers={
                    "apikey": API_KEY,
                    "X-Correlation-ID": correlation_id,
                },
            )

        logger.info(
            "Customer API response | status=%s | task_id=%s | correlation_id=%s",
            response.status_code,
            task.task_id,
            correlation_id,
        )

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail={
                "error": "CUSTOMER_API_TIMEOUT",
                "message": "Customer API request timed out",
            },
        )

    except httpx.RequestError as exc:
        logger.error(
            "Customer API request error | error=%s | task_id=%s | correlation_id=%s",
            str(exc),
            task.task_id,
            correlation_id,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "CUSTOMER_API_UNAVAILABLE",
                "message": "Customer API is unavailable",
            },
        )

    if response.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "CUSTOMER_NOT_FOUND",
                "message": f"Customer {task.customer_id} was not found",
            },
        )

    if response.status_code == 429:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "CUSTOMER_API_RATE_LIMITED",
                "message": "Customer API rate limit was exceeded",
            },
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "CUSTOMER_API_ERROR",
                "message": "Customer API request failed",
            },
        )

    customer = response.json()

    logger.info(
        "A2A task completed | task_id=%s | customer_id=%s | correlation_id=%s",
        task.task_id,
        task.customer_id,
        correlation_id,
    )

    return {
        "task_id": task.task_id,
        "status": "completed",
        "agent": "customer-agent",
        "action": task.action,
        "result": customer,
        "correlation_id": correlation_id,
    }
