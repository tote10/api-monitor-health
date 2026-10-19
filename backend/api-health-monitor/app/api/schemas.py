from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, HttpUrl


class EndpointBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Human-readable name for the endpoint")
    url: HttpUrl = Field(..., description="Target URL to monitor")
    interval_seconds: int = Field(default=60, ge=10, le=3600, description="Check frequency in seconds")
    timeout_seconds: int = Field(default=5, ge=1, le=60, description="HTTP request timeout in seconds")
    expected_status_code: int = Field(default=200, ge=100, le=599, description="Expected HTTP response status code")
    is_active: bool = Field(default=True, description="Whether monitoring is enabled")


class EndpointCreate(EndpointBase):
    pass


class EndpointUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    url: Optional[HttpUrl] = None
    interval_seconds: Optional[int] = Field(default=None, ge=10, le=3600)
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=60)
    expected_status_code: Optional[int] = Field(default=None, ge=100, le=599)
    is_active: Optional[bool] = None


class EndpointResponse(EndpointBase):
    id: int
    created_at: datetime

    model_config = {
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
