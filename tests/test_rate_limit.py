"""Tests for RateLimitService."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.rate_limit import RateLimitService


class TestRateLimitService:
    """Tests for user-scoped daily rate limits."""

    async def test_check_and_increment_allows_until_limit(self, accepted_user_in_db):
        """Test that increments are allowed until the configured limit."""
        RateLimitService._fallback_counters.clear()
        accepted_user_in_db.daily_text_limit = 2
        service = RateLimitService()

        first = await service.check_and_increment(accepted_user_in_db, "daily_text_limit")
        second = await service.check_and_increment(accepted_user_in_db, "daily_text_limit")
        third = await service.check_and_increment(accepted_user_in_db, "daily_text_limit")

        assert first["allowed"] is True
        assert second["allowed"] is True
        assert third["allowed"] is False
        assert third["used"] == 2

    async def test_get_usage_summary(self, accepted_user_in_db):
        """Test summary generation for current daily usage."""
        RateLimitService._fallback_counters.clear()
        service = RateLimitService()
        await service.check_and_increment(accepted_user_in_db, "daily_text_limit")
        await service.check_and_increment(accepted_user_in_db, "daily_ai_limit")

        summary = await service.get_usage_summary(accepted_user_in_db)

        assert summary["daily_text_limit"]["used"] == 1
        assert summary["daily_ai_limit"]["used"] == 1

    async def test_multi_instance_requires_redis_for_rate_limit(self, accepted_user_in_db):
        """Test multi-instance mode fails closed when Redis is unavailable."""
        RateLimitService._fallback_counters.clear()
        service = RateLimitService()

        with patch("app.services.rate_limit.settings.deployment_mode", "multi_instance"):
            service._get_redis = AsyncMock(return_value=None)
            with pytest.raises(RuntimeError) as exc_info:
                await service.check_and_increment(accepted_user_in_db, "daily_text_limit")

        assert "multi-instance" in str(exc_info.value).lower()
