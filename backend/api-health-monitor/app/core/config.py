from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "API Health Monitor"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    AUTH_SECURE_COOKIE: bool = False
    MONITORING_ENABLED: bool = True
    SCHEDULER_POLL_SECONDS: float = Field(default=5.0, ge=1.0, le=300.0)
    CHECK_HISTORY_RETENTION_DAYS: int = Field(default=30, ge=0, le=3650)
    CHECK_HISTORY_CLEANUP_INTERVAL_SECONDS: int = Field(
        default=3600, ge=60, le=86400
    )

    TLS_EXPIRY_WARNING_DAYS: int = Field(default=14, ge=0, le=30)
    ALLOW_LOOPBACK_TARGETS: bool = False
    MONITOR_CONFIG_KEY: SecretStr | None = None
    PUBLIC_BASE_URL: str = ""

    ALERT_WEBHOOK_URL: SecretStr | None = None
    ALERT_EMAIL_TO: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_FROM: str = ""
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: SecretStr | None = None
    SMTP_SECURITY: Literal["starttls", "tls", "plain"] = "starttls"

    @model_validator(mode="after")
    def validate_email_alert_settings(self):
        if self.ALLOW_LOOPBACK_TARGETS and self.ENVIRONMENT != "development":
            raise ValueError("ALLOW_LOOPBACK_TARGETS requires ENVIRONMENT=development")
        if self.SMTP_HOST and not (self.SMTP_FROM and self.ALERT_EMAIL_TO):
            raise ValueError("Set SMTP_FROM and ALERT_EMAIL_TO when SMTP_HOST is configured")
        if self.SMTP_HOST and bool(self.SMTP_USERNAME) != bool(self.SMTP_PASSWORD):
            raise ValueError("Set both SMTP_USERNAME and SMTP_PASSWORD for SMTP sign-in")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )


settings = Settings()
