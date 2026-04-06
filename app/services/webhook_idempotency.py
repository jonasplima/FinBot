"""Webhook idempotency service with Redis and in-memory fallback."""

import logging
from datetime import datetime, timedelta

from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class WebhookIdempotencyService:
    """Prevent duplicate processing of the same webhook message ID."""

    _fallback_processed_ids: dict[str, datetime] = {}

    def __init__(self) -> None:
        self.redis_url = settings.redis_url
        self.ttl_seconds = settings.effective_webhook_idempotency_ttl_seconds
        self._redis: Redis | None = None

    async def reserve(self, message_id: str) -> bool:
        """Reserve a message ID for processing if it has not been seen before."""
        if not message_id:
            return False

        key = self._build_key(message_id)
        redis_client = await self._get_redis()
        if redis_client is not None:
            try:
                reserved = await redis_client.set(key, "1", ex=self.ttl_seconds, nx=True)
                return bool(reserved)
            except Exception as exc:
                logger.warning(f"Redis unavailable for webhook reserve, using fallback: {exc}")

        self._cleanup_fallback()
        if key in self._fallback_processed_ids:
            return False

        self._fallback_processed_ids[key] = datetime.now() + timedelta(seconds=self.ttl_seconds)
        return True

    async def release(self, message_id: str) -> None:
        """Release a reservation after a failed processing attempt."""
        if not message_id:
            return

        key = self._build_key(message_id)
        self._fallback_processed_ids.pop(key, None)

        redis_client = await self._get_redis()
        if redis_client is None:
            return

        try:
            await redis_client.delete(key)
        except Exception as exc:
            logger.warning(f"Redis unavailable for webhook release: {exc}")

    async def _get_redis(self) -> Redis | None:
        """Lazily initialize Redis client."""
        if self._redis is not None:
            return self._redis

        try:
            self._redis = Redis.from_url(self.redis_url, decode_responses=True)
            return self._redis
        except Exception as exc:
            logger.warning(f"Could not initialize Redis client for webhook idempotency: {exc}")
            return None

    def _build_key(self, message_id: str) -> str:
        """Build idempotency storage key."""
        return f"finbot:webhook:{message_id}"

    def _cleanup_fallback(self) -> None:
        """Remove expired in-memory fallback entries."""
        now = datetime.now()
        self._fallback_processed_ids = {
            key: expires_at
            for key, expires_at in self._fallback_processed_ids.items()
            if expires_at > now
        }
