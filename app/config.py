"""Application configuration from environment variables."""

from functools import lru_cache

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

    # WhatsApp
    owner_phone: str
    allowed_numbers: str = ""

    # Gemini AI
    gemini_api_key: str

    # Security
    admin_secret: str

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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra env vars used by Docker (POSTGRES_*, REDIS_*)

    @property
    def allowed_phones(self) -> list[str]:
        """Get list of allowed phone numbers."""
        phones = [self.owner_phone]
        if self.allowed_numbers:
            phones.extend(num.strip() for num in self.allowed_numbers.split(",") if num.strip())
        return list(set(phones))


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
