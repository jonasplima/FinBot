"""Application configuration from environment variables."""

from functools import lru_cache
from typing import Any

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

    # Gemini AI
    gemini_api_key: str

    # Security
    admin_secret: str
    webhook_secret: str = ""

    # Scheduler
    scheduler_enabled: bool = True
    scheduler_timezone: str = "America/Sao_Paulo"
    scheduler_hour: int = 8
    scheduler_minute: int = 0

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


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
