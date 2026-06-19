from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import get_settings

settings = get_settings()


def client_ip(request: Request) -> str:
    """Resolve the real client IP for rate-limiting purposes.

    The app runs behind a Caddy reverse proxy, so the TCP peer is always
    Caddy's address. Caddy's ``reverse_proxy`` appends the originating client
    IP to ``X-Forwarded-For``; the left-most entry is the original client.
    Falls back to the socket address when the header is absent (e.g. direct
    access in local development or tests).
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return get_remote_address(request)


# Shared limiter instance. The default limit is applied to routes decorated
# with @limiter.limit(...) and can be tuned via the AUTH_RATE_LIMIT setting.
limiter = Limiter(
    key_func=client_ip,
    default_limits=[settings.auth_rate_limit],
    enabled=settings.environment != "test",
)
