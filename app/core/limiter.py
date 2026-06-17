from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

settings = get_settings()

# Shared limiter instance. The default limit is applied to routes decorated
# with @limiter.limit(...) and can be tuned via the AUTH_RATE_LIMIT setting.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.auth_rate_limit],
    enabled=settings.environment != "test",
)
