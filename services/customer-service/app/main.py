from fastapi import FastAPI, HTTPException
import logging
import os
import psycopg
import re
import uuid


app = FastAPI(
    title="Partilon Customer Service",
    version="1.0.0",
    description="Customer REST API for the Partilon Technical Assessment",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("customer-service")


@app.middleware("http")
async def correlation_logging(request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex

    response = await call_next(request)

    logger.info(
        "request completed | correlation_id=%s | method=%s | path=%s | status=%s",
        correlation_id,
        request.method,
        request.url.path,
        response.status_code,
    )

    response.headers["X-Correlation-ID"] = correlation_id

    return response

CUSTOMER_ID_PATTERN = re.compile(r"^C\d{3}$")


def require_valid_customer_id(customer_id: str):
    if not CUSTOMER_ID_PATTERN.match(customer_id):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_CUSTOMER_ID",
                "message": "Customer ID must look like C001",
            },
        )


def get_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST", "customer-db"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "customer_db"),
        user=os.getenv("DB_USER", "customer_user"),
        password=os.getenv("DB_PASSWORD", "customer_password"),
    )


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/customers/{customer_id}")
def get_customer(customer_id: str):
    require_valid_customer_id(customer_id)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT customer_id, name, email
                FROM customers
                WHERE customer_id = %s
                """,
                (customer_id,),
            )

            customer = cur.fetchone()

    if customer is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "CUSTOMER_NOT_FOUND",
                "message": f"Customer {customer_id} was not found",
            },
        )

    return {
        "customer_id": customer[0],
        "name": customer[1],
        "email": customer[2],
    }
