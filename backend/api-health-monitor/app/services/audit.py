from sqlalchemy.orm import Session

from app.db.models import AuditEvent


def audit(db: Session, actor: str, owner_id: int | None, action: str, subject_id: int | None = None):
    db.add(AuditEvent(actor=actor, owner_id=owner_id, action=action, subject_id=subject_id))
