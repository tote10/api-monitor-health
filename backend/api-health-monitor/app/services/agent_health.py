from datetime import datetime, timedelta

from app.db.models import PrivateAgent

AGENT_OFFLINE_AFTER = timedelta(seconds=90)


def agent_is_online(agent: PrivateAgent | None, now: datetime) -> bool:
    return bool(agent and agent.token_hash and agent.last_seen_at and now - agent.last_seen_at < AGENT_OFFLINE_AFTER)
