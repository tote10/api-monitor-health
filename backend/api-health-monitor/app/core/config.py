from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "API Health Monitor"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    MONITORING_ENABLED: bool = True
    SCHEDULER_POLL_SECONDS: float = Field(default=5.0, ge=1.0, le=300.0)
    CHECK_HISTORY_RETENTION_DAYS: int = Field(default=30, ge=0, le=3650)
    CHECK_HISTORY_CLEANUP_INTERVAL_SECONDS: int = Field(
        default=3600, ge=60, le=86400
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )


settings = Settings()
