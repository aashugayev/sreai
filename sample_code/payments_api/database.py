"""Small database-access example for incident investigation exercises."""


class ConnectionPool:
    """Represent a deliberately simple connection pool."""

    def __init__(self, max_connections=10):
        self.max_connections = max_connections
        self.active_connections = 0

    def acquire(self):
        """Acquire a connection or raise the sample pool-exhaustion error."""
        if self.active_connections >= self.max_connections:
            raise TimeoutError(
                'database connection pool exhausted after waiting 30 seconds'
            )
        self.active_connections += 1
        return self.active_connections

    def release(self):
        """Release one active connection."""
        if self.active_connections > 0:
            self.active_connections -= 1
