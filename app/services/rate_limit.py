"""Rate limiting service with Redis and in-memory fallback."""

import logging
from datetime import date

from redis.asyncio import Redis

from app.config import get_settings
from app.database.models import User
from app.utils.validators import normalize_phone

logger = logging.getLogger(__name__)
settings = get_settings()


class RateLimitService:
    """Rate limiting service for user-scoped daily limits."""

    _fallback_counters: dict[str, int] = {}

    def __init__(self) -> None:
        self.redis_url = settings.redis_url
        self._redis: Redis | None = None

    async def check_and_increment(
        self,
        user: User,
        limit_field: str,
        increment: int = 1,
    ) -> dict:
        """Check and increment usage for a user limit."""
        if not user.limits_enabled:
            return {"allowed": True, "used": 0, "limit": 0, "remaining": 0}

        limit_value = getattr(user, limit_field)
        key = self._build_key(user.phone, limit_field)
        current_value = await self._get_current_value(key)

        if current_value + increment > limit_value:
            return {
                "allowed": False,
                "used": current_value,
                "limit": limit_value,
                "remaining": max(limit_value - current_value, 0),
            }

        new_value = await self._increment_value(key, increment)
        return {
            "allowed": True,
            "used": new_value,
            "limit": limit_value,
            "remaining": max(limit_value - new_value, 0),
        }

    async def get_usage_summary(self, user: User) -> dict[str, dict[str, int]]:
        """Return current daily usage counters for all tracked limits."""
        summary = {}
        for limit_field in ("daily_text_limit", "daily_media_limit", "daily_ai_limit"):
            key = self._build_key(user.phone, limit_field)
            current_value = await self._get_current_value(key)
            limit_value = getattr(user, limit_field)
            summary[limit_field] = {
                "used": current_value,
                "limit": limit_value,
                "remaining": max(limit_value - current_value, 0),
            }
        return summary

    def format_limit_reached_message(self, limit_field: str, usage: dict) -> str:
        """Format a friendly message when a daily limit is reached."""
        label_map = {
            "daily_text_limit": "mensagens de texto",
            "daily_media_limit": "midias/documentos",
            "daily_ai_limit": "chamadas de IA",
        }
        label = label_map.get(limit_field, limit_field)
        return (
            f"Voce atingiu seu limite diario de {label}: {usage['used']}/{usage['limit']}.\n"
            "Envie 'meus limites' para consultar seus limites atuais ou ajuste com "
            "'ajustar limite de ia para 30 por dia'."
        )

    async def _get_current_value(self, key: str) -> int:
        """Get current counter value using Redis or fallback storage."""
        redis_client = await self._get_redis()
        if redis_client is None:
            return self._fallback_counters.get(key, 0)

        try:
            value = await redis_client.get(key)
            return int(value) if value is not None else 0
        except Exception as e:
            logger.warning(f"Redis unavailable for get, using fallback: {e}")
            return self._fallback_counters.get(key, 0)

    async def _increment_value(self, key: str, increment: int) -> int:
        """Increment counter using Redis or fallback storage."""
        redis_client = await self._get_redis()
        if redis_client is None:
            self._fallback_counters[key] = self._fallback_counters.get(key, 0) + increment
            return self._fallback_counters[key]

        try:
            new_value = await redis_client.incrby(key, increment)
            await redis_client.expire(key, 60 * 60 * 24 * 2)
            return int(new_value)
        except Exception as e:
            logger.warning(f"Redis unavailable for incr, using fallback: {e}")
            self._fallback_counters[key] = self._fallback_counters.get(key, 0) + increment
            return self._fallback_counters[key]

    async def _get_redis(self) -> Redis | None:
        """Lazily initialize Redis client."""
        if self._redis is not None:
            return self._redis

        try:
            self._redis = Redis.from_url(self.redis_url, decode_responses=True)
            return self._redis
        except Exception as e:
            logger.warning(f"Could not initialize Redis client: {e}")
            return None

    def _build_key(self, phone: str, limit_field: str) -> str:
        """Build daily rate limit key."""
        normalized_phone = normalize_phone(phone)
        return f"finbot:rate:{limit_field}:{normalized_phone}:{date.today().isoformat()}"
