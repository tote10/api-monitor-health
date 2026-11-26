from datetime import datetime
from typing import Annotated, Optional

import httpx
from pydantic import AfterValidator, BaseModel, Field, HttpUrl

from app.services.monitor_targets import UnsafeMonitorURL, validate_target


def validate_endpoint_url(value: HttpUrl) -> HttpUrl:
    try:
        validate_target(httpx.URL(str(value)))
    except httpx.InvalidURL as exc:
        raise UnsafeMonitorURL("Invalid monitoring URL") from exc
    return value


MonitorURL = Annotated[HttpUrl, AfterValidator(validate_endpoint_url)]


class EndpointBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Human-readable name for the endpoint")
    url: HttpUrl = Field(..., description="Target URL to monitor")
    interval_seconds: int = Field(default=60, ge=10, le=3600, description="Check frequency in seconds")
    timeout_seconds: int = Field(default=5, ge=1, le=60, description="HTTP request timeout in seconds")
    expected_status_code: int = Field(default=200, ge=100, le=599, description="Expected HTTP response status code")
    is_active: bool = Field(default=True, description="Whether monitoring is enabled")


class EndpointCreate(EndpointBase):
    url: MonitorURL = Field(..., description="Public HTTP or HTTPS URL to monitor")


class EndpointUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    url: Optional[MonitorURL] = None
    interval_seconds: Optional[int] = Field(default=None, ge=10, le=3600)
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=60)
    expected_status_code: Optional[int] = Field(default=None, ge=100, le=599)
    is_active: Optional[bool] = None


class EndpointResponse(EndpointBase):
    id: int
    created_at: datetime

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": 1,
                "name": "Production User API",
                "url": "https://api.example.com/health",
                "interval_seconds": 60,
                "timeout_seconds": 5,
                "expected_status_code": 200,
                "is_active": True,
                "created_at": "2026-10-13T12:00:00Z"
            }
        }
    }


class CheckResultResponse(BaseModel):
    id: int
    endpoint_id: int
    checked_at: datetime
    is_success: bool
    status_code: Optional[int] = None
    response_time_ms: Optional[int] = None
    error: Optional[str] = None

    model_config = {"from_attributes": True}


class EndpointStatusResponse(BaseModel):
    endpoint_id: int
    is_up: Optional[bool] = None
    last_checked_at: Optional[datetime] = None
    last_status_code: Optional[int] = None
    last_response_time_ms: Optional[int] = None
    last_error: Optional[str] = None
    total_checks: int
    successful_checks: int
    uptime_percentage: Optional[float] = None


class EndpointIncidentResponse(BaseModel):
    first_failed_at: datetime
    recovered_at: datetime | None
    duration_seconds: int
    started_within_history: bool
    first_error: str | None
