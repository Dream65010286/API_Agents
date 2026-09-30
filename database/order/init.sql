CREATE TABLE IF NOT EXISTS orders (
    order_id VARCHAR(20) PRIMARY KEY,
    customer_id VARCHAR(20) NOT NULL,
    order_date TIMESTAMP NOT NULL,
    status VARCHAR(30) NOT NULL,
    total_amount NUMERIC(10, 2) NOT NULL
);

INSERT INTO orders (order_id, customer_id, order_date, status, total_amount)
VALUES
    ('O1001', 'C001', '2026-09-25 10:30:00', 'SHIPPED', 1299.00),
    ('O1002', 'C001', '2026-09-28 14:15:00', 'DELIVERED', 799.00),
    ('O1003', 'C002', '2026-09-29 09:00:00', 'PROCESSING', 2499.00)
ON CONFLICT (order_id) DO NOTHING;
