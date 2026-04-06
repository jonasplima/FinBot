"""Rate limiting for administrative HTTP endpoints."""

import logging
from datetime import datetime, timedelta

from redis.asyncio import Redis

from app.config import get_settings
from app.services.operational_status import OperationalStatusService

logger = logging.getLogger(__name__)
settings = get_settings()
operational_status = OperationalStatusService()


class AdminRateLimitService:
    """Rate limiting service for administrative HTTP access."""

    _fallback_counters: dict[str, tuple[int, datetime]] = {}

    def __init__(self) -> None:
        self.redis_url = settings.redis_url
        self.max_attempts = settings.admin_rate_limit_max_attempts
        self.window_seconds = settings.effective_admin_rate_limit_window_seconds
        self._redis: Redis | None = None

    def _allow_local_fallback(self) -> bool:
        """Whether local in-memory fallback is acceptable for this deployment mode."""
        return settings.normalized_deployment_mode == "single_instance"

    async def check_request(self, identifier: str) -> dict[str, int | bool]:
        """Check and increment admin request count for the current window."""
        key = self._build_key(identifier)
        redis_client = await self._get_redis()
        if redis_client is not None:
            try:
                count = await redis_client.incr(key)
                if count == 1:
                    await redis_client.expire(key, self.window_seconds)
                return self._format_response(int(count))
            except Exception as exc:
                if not self._allow_local_fallback():
                    operational_status.record_event(
                        "admin_rate_limit",
                        "error",
                        "Administrative rate-limit storage unavailable in multi-instance mode.",
                    )
                    raise RuntimeError("Admin rate-limit storage unavailable in multi-instance mode.") from exc
                logger.warning(f"Redis unavailable for admin rate limit, using fallback: {exc}")
                operational_status.record_event(
                    "admin_rate_limit",
                    "warning",
                    "Redis unavailable; using local fallback for admin rate limit in single-instance mode.",
                )

        if not self._allow_local_fallback():
            operational_status.record_event(
                "admin_rate_limit",
                "error",
                "Administrative rate-limit storage unavailable in multi-instance mode.",
            )
            raise RuntimeError("Admin rate-limit storage unavailable in multi-instance mode.")

        now = datetime.now()
        current_count, expires_at = self._fallback_counters.get(
            key,
            (0, now + timedelta(seconds=self.window_seconds)),
        )
        if expires_at <= now:
            current_count = 0
            expires_at = now + timedelta(seconds=self.window_seconds)

        current_count += 1
        self._fallback_counters[key] = (current_count, expires_at)
        return self._format_response(current_count)

    async def _get_redis(self) -> Redis | None:
        """Lazily initialize Redis client."""
        if self._redis is not None:
            return self._redis

        try:
            self._redis = Redis.from_url(self.redis_url, decode_responses=True)
            return self._redis
        except Exception as exc:
            logger.warning(f"Could not initialize Redis client for admin rate limit: {exc}")
            return None

    def _build_key(self, identifier: str) -> str:
        """Build storage key for the current admin request window."""
        now = int(datetime.now().timestamp())
        bucket = now // self.window_seconds
        return f"finbot:admin-rate:{identifier}:{bucket}"

    def _format_response(self, count: int) -> dict[str, int | bool]:
        """Build normalized response for admin rate limiting."""
        allowed = count <= self.max_attempts
        return {
            "allowed": allowed,
            "used": count,
            "limit": self.max_attempts,
            "retry_after": self.window_seconds,
        }
