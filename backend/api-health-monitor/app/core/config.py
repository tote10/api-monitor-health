from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "API Health Monitor"
    VERSION: str = "0.2.0"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    MONITORING_ENABLED: bool = True
    SCHEDULER_POLL_SECONDS: float = Field(default=5.0, ge=1.0, le=300.0)
    CHECK_HISTORY_RETENTION_DAYS: int = Field(default=30, ge=0, le=3650)
    CHECK_HISTORY_CLEANUP_INTERVAL_SECONDS: int = Field(
        default=3600, ge=60, le=86400
    )

    AUTH_ENABLED: bool = True
    ALLOW_REGISTRATION: bool = True
    SESSION_COOKIE_NAME: str = "ahm_session"
    SESSION_TTL_HOURS: int = Field(default=168, ge=1, le=8760)
    SESSION_COOKIE_SECURE: bool = False

    SSRF_PROTECTION_ENABLED: bool = True
    ALLOW_PRIVATE_TARGETS: bool = False

    ALERT_COOLDOWN_MINUTES: int = Field(default=15, ge=0, le=1440)
    SMTP_HOST: str | None = None
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM: str = "api-health-monitor@localhost"
    SMTP_USE_TLS: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )


settings = Settings()
