"""Application configuration from environment variables."""

import os
from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    port: int = 3003
    log_level: str = "INFO"

    # Database
    database_url: str

    # Redis
    redis_url: str

    # Evolution API
    evolution_api_url: str
    evolution_api_key: str
    evolution_instance: str

    # WhatsApp / Evolution bootstrap
    owner_phone: str = ""
    allowed_numbers: str = ""

    # AI providers
    gemini_api_key: str
    ai_timeout_seconds: int = Field(
        default=25,
        validation_alias=AliasChoices("AI_TIMEOUT_SECONDS", "GEMINI_TIMEOUT_SECONDS"),
    )
    groq_api_key: str = ""
    ai_primary_provider: str = "gemini"

    # Security
    admin_secret: str
    webhook_secret: str = ""
    app_encryption_key: str = ""
    admin_rate_limit_max_attempts: int = 10
    admin_rate_limit_window_seconds: int = 60
    web_session_ttl_hours: int = 720

    # Scheduler
    scheduler_enabled: bool = True
    scheduler_timezone: str = "America/Sao_Paulo"
    scheduler_hour: int = 8
    scheduler_minute: int = 0
    deployment_mode: str = "single_instance"
    scheduler_lock_ttl_seconds: int = 1800
    instance_id: str = os.getenv("INSTANCE_ID", "")

    # Currency Conversion - Wise API (primary)
    wise_api_url: str = "https://api.wise.com"
    wise_api_key: str = ""

    # Currency Conversion - ExchangeRate API (fallback)
    exchange_rate_api_url: str = "https://v6.exchangerate-api.com/v6"
    exchange_rate_api_key: str = ""

    # Currency cache settings
    exchange_rate_cache_ttl: int = 3600  # 1 hour in seconds
    fallback_rates_update_days: int = 7  # Update fallback rates in database weekly

    # Terms and multi-user defaults
    terms_version: str = "2026-04"
    default_daily_text_limit: int = 100
    default_daily_media_limit: int = 20
    default_daily_ai_limit: int = 50

    # Defensive limits - PDFs and backups
    max_pdf_size_bytes: int = 2_000_000
    max_pdf_pages: int = 10
    max_pdf_text_chars: int = 20_000
    max_backup_size_bytes: int = 1_000_000
    max_backup_expenses: int = 5_000
    max_backup_budgets: int = 200
    max_backup_goals: int = 200
    max_backup_budget_alerts: int = 1_000
    max_backup_goal_updates: int = 1_000
    backup_temp_ttl_seconds: int = 600
    webhook_idempotency_ttl_seconds: int = 172_800

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra env vars used by Docker (POSTGRES_*, REDIS_*)

    @property
    def allowed_phones(self) -> list[str]:
        """Get optional list of allowed phone numbers for controlled rollout."""
        if not self.allowed_numbers:
            return []

        phones = [num.strip() for num in self.allowed_numbers.split(",") if num.strip()]
        return list(set(phones))

    def user_limit_defaults(self) -> dict[str, Any]:
        """Return default daily limits for newly created users."""
        return {
            "daily_text_limit": self.default_daily_text_limit,
            "daily_media_limit": self.default_daily_media_limit,
            "daily_ai_limit": self.default_daily_ai_limit,
        }

    @property
    def effective_max_pdf_size_bytes(self) -> int:
        """Clamp PDF size limit to a safe server ceiling."""
        return min(max(self.max_pdf_size_bytes, 1), 5_000_000)

    @property
    def effective_max_pdf_pages(self) -> int:
        """Clamp PDF page limit to a safe server ceiling."""
        return min(max(self.max_pdf_pages, 1), 20)

    @property
    def effective_max_pdf_text_chars(self) -> int:
        """Clamp PDF extracted text limit to a safe server ceiling."""
        return min(max(self.max_pdf_text_chars, 100), 50_000)

    @property
    def effective_max_backup_size_bytes(self) -> int:
        """Clamp backup JSON size limit to a safe server ceiling."""
        return min(max(self.max_backup_size_bytes, 1_024), 5_000_000)

    @property
    def effective_max_backup_expenses(self) -> int:
        """Clamp backup expense count limit to a safe server ceiling."""
        return min(max(self.max_backup_expenses, 1), 10_000)

    @property
    def effective_max_backup_budgets(self) -> int:
        """Clamp backup budget count limit to a safe server ceiling."""
        return min(max(self.max_backup_budgets, 1), 500)

    @property
    def effective_max_backup_goals(self) -> int:
        """Clamp backup goal count limit to a safe server ceiling."""
        return min(max(self.max_backup_goals, 1), 500)

    @property
    def effective_max_backup_budget_alerts(self) -> int:
        """Clamp backup budget alert count limit to a safe server ceiling."""
        return min(max(self.max_backup_budget_alerts, 1), 5_000)

    @property
    def effective_max_backup_goal_updates(self) -> int:
        """Clamp backup goal update count limit to a safe server ceiling."""
        return min(max(self.max_backup_goal_updates, 1), 5_000)

    @property
    def effective_backup_temp_ttl_seconds(self) -> int:
        """Clamp temporary backup TTL to a safe server ceiling."""
        return min(max(self.backup_temp_ttl_seconds, 60), 3_600)

    @property
    def effective_webhook_idempotency_ttl_seconds(self) -> int:
        """Clamp webhook idempotency TTL to a safe server ceiling."""
        return min(max(self.webhook_idempotency_ttl_seconds, 3_600), 604_800)

    @property
    def effective_ai_timeout_seconds(self) -> int:
        """Clamp AI timeout to a safe server ceiling."""
        return min(max(self.ai_timeout_seconds, 5), 120)

    @property
    def normalized_ai_primary_provider(self) -> str:
        """Normalize the preferred primary AI provider."""
        provider = self.ai_primary_provider.strip().lower()
        return provider if provider in {"gemini", "groq"} else "gemini"

    @property
    def effective_admin_rate_limit_window_seconds(self) -> int:
        """Clamp admin rate-limit window to a safe operational range."""
        return min(max(self.admin_rate_limit_window_seconds, 10), 3600)

    @property
    def effective_scheduler_lock_ttl_seconds(self) -> int:
        """Clamp scheduler lock TTL to a safe operational window."""
        return min(max(self.scheduler_lock_ttl_seconds, 60), 86_400)

    @property
    def effective_instance_id(self) -> str:
        """Return a stable instance identifier when configured."""
        return self.instance_id.strip()

    @property
    def normalized_deployment_mode(self) -> str:
        """Normalize deployment mode to supported values."""
        mode = self.deployment_mode.strip().lower()
        return mode if mode in {"single_instance", "multi_instance"} else "single_instance"

    @property
    def effective_web_session_ttl_hours(self) -> int:
        """Clamp web-session TTL to a sane operational range."""
        return min(max(self.web_session_ttl_hours, 1), 24 * 90)

    @property
    def effective_app_encryption_key_material(self) -> str:
        """Return key material for deriving application encryption keys."""
        candidates = [
            self.app_encryption_key.strip(),
            f"{self.admin_secret}:{self.webhook_secret}".strip(":"),
            self.admin_secret.strip(),
        ]
        for candidate in candidates:
            if candidate:
                return candidate
        raise ValueError("No application encryption key material is configured.")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
