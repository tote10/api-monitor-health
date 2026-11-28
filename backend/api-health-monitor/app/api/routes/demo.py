import asyncio
import secrets
from datetime import datetime, timezone
from time import monotonic
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes.email_settings import check_write_origin
from app.core.auth import check_request_origin
from app.core.config import settings
from app.db.database import Base
from app.db.models import EmailMonitor, EmailRecipient, Endpoint, Incident, User
from app.services.alert_service import deliver_pending
from app.services.email_content import build_message
from app.services.incident_service import incident_timeline
from app.services.monitor_service import check_endpoint, get_status_summary, perform_check, recent_check_series
from app.web_pages import page_response


router = APIRouter(prefix="/demo", include_in_schema=False)
COOKIE = "monitor_demo"
DEMO_URL = "http://portfolio-demo.invalid/health"
DEMO_RECIPIENT = "visitor@example.test"
DEMO_TTL = 30 * 60
MAX_DEMOS = 100
MAX_CHECKS = 100


class PortfolioDemo:
    def __init__(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.lock = asyncio.Lock()
        self.expires_at = monotonic() + DEMO_TTL
        self.available = True
        self.messages: dict[int, dict] = {}
        self.checks = 0
        with self.session() as db:
            user = User(username="portfolio-demo", role="member", password_hash="disabled")
            db.add(user)
            db.flush()
            endpoint = Endpoint(name="Demo API", url=DEMO_URL, owner_id=user.id, interval_seconds=3600)
            recipient = EmailRecipient(
                address=DEMO_RECIPIENT, scope=f"user:{user.id}", user_id=user.id,
                verified_at=datetime.now(timezone.utc),
            )
            db.add_all([endpoint, recipient])
            db.flush()
            db.add(EmailMonitor(endpoint_id=endpoint.id, recipient_id=recipient.id))
            self.endpoint_id = endpoint.id
            db.commit()

    async def run_check(self):
        def target(request: httpx.Request):
            if request.method != "GET" or str(request.url) != DEMO_URL:
                raise RuntimeError("Unexpected demo request")
            return httpx.Response(200 if self.available else 503, json={"available": self.available})

        async with httpx.AsyncClient(transport=httpx.MockTransport(target), trust_env=False) as client:
            async def fetch(_, url, timeout, *, method, headers, body, read_body):
                return await client.request(method, url, timeout=timeout, headers=headers, content=body)

            async def probe(endpoint, check_client):
                return await check_endpoint(endpoint, check_client, fetch=fetch)

            with self.session() as db:
                await perform_check(db, db.get(Endpoint, self.endpoint_id), probe=probe, demo=True)
        self.checks += 1
        await deliver_pending(self.session, capture=self.capture)

    def capture(self, payload, delivery_id, recipient):
        if recipient != DEMO_RECIPIENT:
            raise RuntimeError("Unexpected demo recipient")
        message = build_message(payload, recipient, "monitor@example.test", f"<demo-{delivery_id}@example.test>")
        self.messages[delivery_id] = {
            "id": delivery_id, "subject": message["Subject"], "to": message["To"],
            "text": message.get_body(preferencelist=("plain",)).get_content(),
            "html": message.get_body(preferencelist=("html",)).get_content(),
        }

    def snapshot(self) -> dict:
        with self.session() as db:
            incident = db.scalar(select(Incident).where(
                Incident.endpoint_id == self.endpoint_id,
            ).order_by(Incident.id.desc()).limit(1))
            overview = get_status_summary(db, self.endpoint_id)
            recent = recent_check_series(db, [self.endpoint_id], datetime.now(timezone.utc))
            return {
                "service": "Demo API", "status": overview.model_dump(),
                "recent_checks": recent.get(self.endpoint_id, []),
                "active_incident": bool(incident and incident.recovered_at is None),
                "timeline": incident_timeline(db, incident) if incident else None,
                "messages": list(reversed(self.messages.values()))[:20],
            }


sessions: dict[str, PortfolioDemo] = {}


def demo_enabled():
    if not settings.DEMO_ENABLED:
        raise HTTPException(404, "Demo is unavailable")


def find_demo(request: Request) -> PortfolioDemo | None:
    now = monotonic()
    for token, demo in list(sessions.items()):
        if demo.expires_at <= now and not demo.lock.locked():
            demo.engine.dispose()
            del sessions[token]
    return sessions.get(request.cookies.get(COOKIE, ""))


@router.get("", dependencies=[Depends(demo_enabled)])
def demo_page():
    return page_response("demo.html", headers={"X-Robots-Tag": "noindex"})


@router.get("/state", dependencies=[Depends(demo_enabled), Depends(check_request_origin)])
async def demo_state(request: Request, response: Response):
    demo = find_demo(request)
    if demo is None:
        if len(sessions) >= MAX_DEMOS:
            raise HTTPException(503, "The demo is busy. Try again later.")
        token = secrets.token_urlsafe(32)
        demo = sessions[token] = PortfolioDemo()
        response.set_cookie(
            COOKIE, token, max_age=DEMO_TTL, path="/demo", secure=settings.AUTH_SECURE_COOKIE,
            httponly=True, samesite="strict",
        )
    response.headers["Cache-Control"] = "no-store"
    async with demo.lock:
        if not demo.checks:
            await demo.run_check()
        return demo.snapshot()


@router.post("/{action}", dependencies=[Depends(demo_enabled), Depends(check_write_origin)])
async def demo_action(action: Literal["outage", "restore"], request: Request, response: Response):
    demo = find_demo(request)
    if demo is None:
        raise HTTPException(410, "This demo session expired. Reload the page to start again.")
    response.headers["Cache-Control"] = "no-store"
    async with demo.lock:
        if demo.checks >= MAX_CHECKS:
            raise HTTPException(429, "This demo session has reached its check limit.")
        demo.available = action == "restore"
        await demo.run_check()
        return demo.snapshot()
