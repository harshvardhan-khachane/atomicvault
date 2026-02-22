class AtomicVaultError(Exception):
    """Base class for all AtomicVault domain exceptions."""
    pass


class StorageError(AtomicVaultError):
    """Raised when underlying storage (Redis/MinIO) fails unexpectedly."""
    pass


class FileTooLargeError(AtomicVaultError):
    """Raised when an uploaded file exceeds the configured maximum size."""
    def __init__(self, size_bytes: int, max_size_bytes: int):
        self.size_bytes = size_bytes
        self.max_size_bytes = max_size_bytes
        super().__init__(
            f"File too large: {size_bytes} bytes "
            f"(max {max_size_bytes} bytes)"
        )


class InvalidTTLError(AtomicVaultError):
    """Raised when a requested TTL is out of application bounds."""
    def __init__(self, requested_ttl: int, max_ttl: int):
        self.requested_ttl = requested_ttl
        self.max_ttl = max_ttl
        super().__init__(
            f"TTL must be between 1 and {max_ttl} seconds, got {requested_ttl}"
        )
