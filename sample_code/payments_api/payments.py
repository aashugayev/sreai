"""Payment handling example with an investigation-friendly call path."""

from payments_api.database import ConnectionPool


def create_payment(pool: ConnectionPool, payment: dict) -> dict:
    """Persist a payment using one pooled database connection."""
    connection = pool.acquire()
    try:
        if not payment.get('customer_id') or payment.get('amount', 0) <= 0:
            raise ValueError('payment requires a customer and positive amount')
        return {
            'payment_id': payment['payment_id'],
            'connection_id': connection,
            'status': 'accepted',
        }
    finally:
        pool.release()


def retry_create_payment(pool: ConnectionPool, payment: dict, attempts=2):
    """Retry a payment after a connection acquisition timeout."""
    for attempt in range(attempts):
        try:
            return create_payment(pool, payment)
        except TimeoutError:
            if attempt + 1 == attempts:
                raise
