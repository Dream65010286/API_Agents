CREATE TABLE IF NOT EXISTS customers (
    customer_id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE
);

INSERT INTO customers (customer_id, name, email)
VALUES
    ('C001', 'Alice Johnson', 'alice@example.com'),
    ('C002', 'Bob Smith', 'bob@example.com')
ON CONFLICT (customer_id) DO NOTHING;
