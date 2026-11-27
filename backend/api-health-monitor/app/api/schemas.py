from datetime import datetime, timedelta
from typing import Annotated, Literal, Optional

import httpx
from pydantic import AfterValidator, AwareDatetime, BaseModel, Field, HttpUrl, field_validator, model_validator

from app.services.monitor_targets import UnsafeMonitorURL, validate_target


def validate_endpoint_url(value: HttpUrl) -> HttpUrl:
    try:
        validate_target(httpx.URL(str(value)))
    except httpx.InvalidURL as exc:
        raise UnsafeMonitorURL("Invalid monitoring URL") from exc
    return value


MonitorURL = Annotated[HttpUrl, AfterValidator(validate_endpoint_url)]


Method = Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
Severity = Literal["info", "warning", "critical"]
Tag = Annotated[str, Field(min_length=1, max_length=40)]
StatusCode = Annotated[int, Field(ge=100, le=599)]


class RequestFields(BaseModel):
    headers: dict[str, str] = Field(default_factory=dict, max_length=30)
    body: str | None = Field(default=None, max_length=16000)
    contains: str | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, headers):
        for name, value in headers.items():
            if (not name or len(name) > 100 or not all(c.isascii() and (c.isalnum() or c in "!#$%&'*+-.^_`|~") for c in name)
                    or not value.isascii() or any(ord(c) < 32 or ord(c) >= 127 for c in value)
                    or len(value) > 4096 or name.lower() in {
                        "host", "content-length", "transfer-encoding", "connection",
                        "proxy-authorization", "upgrade", "expect", "te", "trailer", "accept-encoding",
                    }):
                raise ValueError("Invalid or reserved request header")
        return headers


class WorkflowStep(RequestFields):
    url: MonitorURL
    method: Method = "GET"
    timeout_seconds: int = Field(default=5, ge=1, le=60)
    expected_status_codes: list[StatusCode] = Field(default_factory=lambda: [200], min_length=1, max_length=20)
    extract: dict[str, str] = Field(default_factory=dict, max_length=10)

    @field_validator("extract")
    @classmethod
    def validate_extract(cls, variables):
        if any(not key.isascii() or not key.isidentifier() or len(key) > 40
               or not value or len(value) > 200 for key, value in variables.items()):
            raise ValueError("Extraction needs a variable name and a dot-separated JSON path")
        return variables


class RequestOptions(RequestFields):
    steps: list[WorkflowStep] | None = Field(default=None, min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_mode(self):
        if self.steps and (self.headers or self.body is not None or self.contains is not None):
            raise ValueError("Put request options inside each step for workflow checks")
        return self


class EndpointBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Human-readable name for the endpoint")
    url: HttpUrl = Field(..., description="Target URL to monitor")
    interval_seconds: int = Field(default=60, ge=10, le=3600, description="Check frequency in seconds")
    timeout_seconds: int = Field(default=5, ge=1, le=60, description="HTTP request timeout in seconds")
    expected_status_code: int = Field(default=200, ge=100, le=599, description="Expected HTTP response status code")
    method: Method = "GET"
    expected_status_codes: list[StatusCode] | None = Field(default=None, min_length=1, max_length=20)
    latency_budget_ms: int | None = Field(default=None, ge=1, le=60000)
    tags: list[Tag] = Field(default_factory=list, max_length=20)
    service_group: str | None = Field(default=None, min_length=1, max_length=80)
    public_label: str | None = Field(default=None, min_length=1, max_length=100)
    retention_days: int | None = Field(default=None, ge=0, le=3650)
    severity: Severity = "warning"
    slo_target_percentage: float | None = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    slo_window_hours: int = Field(default=720, ge=1, le=720)
    failure_threshold: int = Field(default=1, ge=1, le=20)
    recovery_threshold: int = Field(default=1, ge=1, le=20)
    is_active: bool = Field(default=True, description="Whether monitoring is enabled")


class EndpointCreate(EndpointBase):
    request_options: RequestOptions | None = None
    url: MonitorURL = Field(..., description="Public HTTP or HTTPS URL to monitor")


class EndpointUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    url: Optional[MonitorURL] = None
    interval_seconds: Optional[int] = Field(default=None, ge=10, le=3600)
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=60)
    expected_status_code: Optional[int] = Field(default=None, ge=100, le=599)
    is_active: Optional[bool] = None
    method: Method | None = None
    expected_status_codes: list[StatusCode] | None = Field(default=None, min_length=1, max_length=20)
    latency_budget_ms: int | None = Field(default=None, ge=1, le=60000)
    tags: list[Tag] | None = Field(default=None, max_length=20)
    service_group: str | None = Field(default=None, min_length=1, max_length=80)
    public_label: str | None = Field(default=None, min_length=1, max_length=100)
    retention_days: int | None = Field(default=None, ge=0, le=3650)
    severity: Severity | None = None
    slo_target_percentage: float | None = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    slo_window_hours: int | None = Field(default=None, ge=1, le=720)
    failure_threshold: int | None = Field(default=None, ge=1, le=20)
    recovery_threshold: int | None = Field(default=None, ge=1, le=20)
    request_options: RequestOptions | None = None


class EndpointResponse(EndpointBase):
    interval_seconds: int = Field(ge=10, le=2592000)
    id: int
    created_at: datetime
    has_request_options: bool = False
    kind: str = "http"
    agent_id: int | None = None
    agent_target: str | None = None
    heartbeat_grace_seconds: int | None = None
    last_heartbeat_at: datetime | None = None

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
    failure_kind: str | None = None
    is_slow: bool = False
    failed_step: int | None = None
    certificate_expires_at: datetime | None = None

    model_config = {"from_attributes": True}


class EndpointStatusResponse(BaseModel):
    endpoint_id: int
    is_up: Optional[bool] = None
    last_checked_at: Optional[datetime] = None
    last_status_code: Optional[int] = None
    last_response_time_ms: Optional[int] = None
    last_error: Optional[str] = None
    is_stale: bool = False
    agent_offline: bool = False
    certificate_expires_at: datetime | None = None
    is_slow: bool = False
    total_checks: int
    successful_checks: int
    uptime_percentage: Optional[float] = None


class EndpointIncidentResponse(BaseModel):
    first_failed_at: datetime
    recovered_at: datetime | None
    duration_seconds: int
    started_within_history: bool
    first_error: str | None


class IncidentRecord(BaseModel):
    id: int
    endpoint_id: int
    first_failed_at: datetime
    opened_at: datetime
    recovered_at: datetime | None
    acknowledged_at: datetime | None
    first_error: str | None
    notes: list[dict]
    root_cause: str | None = None
    follow_up: str | None = None
    public_update: str | None = None

    model_config = {"from_attributes": True}


class IncidentNote(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class MaintenanceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def check_dates(self):
        if self.ends_at <= self.starts_at or self.ends_at - self.starts_at > timedelta(days=90):
            raise ValueError("Choose an end time after the start and within 90 days")
        return self


class MaintenanceRecord(MaintenanceCreate):
    id: int
    canceled_at: datetime | None = None

    model_config = {"from_attributes": True}


class DeploymentCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    revision: str | None = Field(default=None, max_length=100)
    deployed_at: AwareDatetime | None = None


class DeploymentRecord(DeploymentCreate):
    id: int
    deployed_at: datetime

    model_config = {"from_attributes": True}



class IncidentReportUpdate(BaseModel):
    root_cause: str | None = Field(default=None, max_length=4000)
    follow_up: str | None = Field(default=None, max_length=4000)
    public_update: str | None = Field(default=None, max_length=1000)
