from datetime import datetime
from typing import Annotated, List, Literal, Optional

from pydantic import AfterValidator, BaseModel, Field, HttpUrl


def _clean_email(value: str) -> str:
    email = value.strip().lower()
    local, _, domain = email.partition("@")
    if len(email) > 254 or not local or "." not in domain or " " in email:
        raise ValueError("Enter a valid email address.")
    return email


EmailAddress = Annotated[str, AfterValidator(_clean_email)]


class EndpointBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Human-readable name for the endpoint")
    url: HttpUrl = Field(..., description="Target URL to monitor")
    interval_seconds: int = Field(default=60, ge=10, le=3600, description="Check frequency in seconds")
    timeout_seconds: int = Field(default=5, ge=1, le=60, description="HTTP request timeout in seconds")
    expected_status_code: int = Field(default=200, ge=100, le=599, description="Expected HTTP response status code")
    is_active: bool = Field(default=True, description="Whether monitoring is enabled")
    alerts_enabled: bool = Field(default=True, description="Send alerts when the endpoint changes state")


class EndpointCreate(EndpointBase):
    pass


class EndpointUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    url: Optional[HttpUrl] = None
    interval_seconds: Optional[int] = Field(default=None, ge=10, le=3600)
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=60)
    expected_status_code: Optional[int] = Field(default=None, ge=100, le=599)
    is_active: Optional[bool] = None
    alerts_enabled: Optional[bool] = None


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


class StatsBucket(BaseModel):
    start: datetime
    checks: int
    failures: int
    uptime_percentage: Optional[float] = None
    avg_response_time_ms: Optional[float] = None


class IncidentSummary(BaseModel):
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_seconds: int
    status_code: Optional[int] = None
    error: Optional[str] = None


class EndpointStatsResponse(BaseModel):
    endpoint_id: int
    window_hours: int
    bucket_hours: int
    total_checks: int
    successful_checks: int
    uptime_percentage: Optional[float] = None
    avg_response_time_ms: Optional[float] = None
    p95_response_time_ms: Optional[float] = None
    buckets: List[StatsBucket]
    incidents: List[IncidentSummary]


class LoginRequest(BaseModel):
    email: EmailAddress
    password: str = Field(..., min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    email: EmailAddress
    name: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=8, max_length=128)


class UserCreate(RegisterRequest):
    role: Literal["admin", "viewer"] = "viewer"


class UserUpdate(BaseModel):
    role: Optional[Literal["admin", "viewer"]] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AuthSessionResponse(BaseModel):
    auth_enabled: bool
    authenticated: bool
    registration_open: bool
    has_users: bool = False
    user: Optional[UserResponse] = None


class ChannelBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    kind: Literal["email", "webhook"]
    target: str = Field(..., min_length=3, max_length=2048)
    is_enabled: bool = True


class ChannelCreate(ChannelBase):
    pass


class ChannelUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    kind: Optional[Literal["email", "webhook"]] = None
    target: Optional[str] = Field(default=None, min_length=3, max_length=2048)
    is_enabled: Optional[bool] = None


class ChannelResponse(ChannelBase):
    id: int
    created_at: datetime
    last_sent_at: Optional[datetime] = None
    last_error: Optional[str] = None

    model_config = {"from_attributes": True}


class ChannelTestResponse(BaseModel):
    sent: bool
    error: Optional[str] = None
