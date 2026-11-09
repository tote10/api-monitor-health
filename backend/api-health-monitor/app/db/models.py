from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Endpoint(Base):
    __tablename__ = "endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(
        String(2048), nullable=False, unique=True
    )

    interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5
    )
    expected_status_code: Mapped[int] = mapped_column(
        Integer, nullable=False, default=200
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )