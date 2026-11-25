from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://partyplay:partyplay@localhost:5432/partyplay_dev"
    jwt_secret: str = "change-me-in-production"
    jwt_expire_minutes: int = 60 * 12
    cors_origins: list[str] = ["http://localhost:5173"]
    cookie_secure: bool = False  # set True in production (HTTPS)

    # Booking rules
    hold_minutes: int = 120  # how long a pending request holds stock
    max_booking_days: int = 30
    timezone: str = "Africa/Nairobi"


@lru_cache
def get_settings() -> Settings:
    return Settings()
