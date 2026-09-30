from fastapi import FastAPI, HTTPException
import logging
import os
import psycopg

app = FastAPI(
    title="Partilon Order Service",
    version="1.0.0",
    description="Order REST API for the Partilon Technical Assessment",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("order-service")


@app.middleware("http")
async def correlation_logging(request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", "missing")

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

@app.middleware("http")
async def correlation_logging(request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", "missing")

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

def get_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST", "order-db"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "order_db"),
        user=os.getenv("DB_USER", "order_user"),
        password=os.getenv("DB_PASSWORD", "order_password"),
    )


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT order_id, customer_id, order_date, status, total_amount
                FROM orders
                WHERE order_id = %s
                """,
                (order_id,),
            )
            order = cur.fetchone()

    if order is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "ORDER_NOT_FOUND",
                "message": f"Order {order_id} was not found",
            },
        )

    return {
        "order_id": order[0],
        "customer_id": order[1],
        "order_date": order[2],
        "status": order[3],
        "total_amount": float(order[4]),
    }


@app.get("/customers/{customer_id}/orders")
def get_customer_orders(customer_id: str):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT order_id, customer_id, order_date, status, total_amount
                FROM orders
                WHERE customer_id = %s
                ORDER BY order_date DESC
                """,
                (customer_id,),
            )
            orders = cur.fetchall()

    return [
        {
            "order_id": order[0],
            "customer_id": order[1],
            "order_date": order[2],
            "status": order[3],
            "total_amount": float(order[4]),
        }
        for order in orders
    ]
