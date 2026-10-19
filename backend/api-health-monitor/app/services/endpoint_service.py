from datetime import datetime, timezone
from typing import List, Optional
from app.api.schemas import EndpointCreate, EndpointUpdate


# Day 1 in-memory mock store before PostgreSQL integration in Day 2
_mock_endpoints_db: dict[int, dict] = {}
_id_counter = 1


def register_endpoint(data: EndpointCreate) -> dict:
    global _id_counter
    endpoint_record = {
        "id": _id_counter,
        "name": data.name,
        "url": str(data.url),
        "interval_seconds": data.interval_seconds,
        "timeout_seconds": data.timeout_seconds,
        "expected_status_code": data.expected_status_code,
        "is_active": data.is_active,
        "created_at": datetime.now(timezone.utc),
    }
    _mock_endpoints_db[_id_counter] = endpoint_record
    _id_counter += 1
    return endpoint_record


def list_endpoints() -> List[dict]:
    return list(_mock_endpoints_db.values())


def get_endpoint_by_id(endpoint_id: int) -> Optional[dict]:
    return _mock_endpoints_db.get(endpoint_id)


def update_endpoint(endpoint_id: int, data: EndpointUpdate) -> Optional[dict]:
    endpoint = _mock_endpoints_db.get(endpoint_id)
    if not endpoint:
        return None

    update_dict = data.model_dump(exclude_unset=True)
    if "url" in update_dict and update_dict["url"] is not None:
        update_dict["url"] = str(update_dict["url"])

    endpoint.update(update_dict)
    return endpoint


def delete_endpoint(endpoint_id: int) -> bool:
    if endpoint_id in _mock_endpoints_db:
        del _mock_endpoints_db[endpoint_id]
        return True
    return False


def reset_mock_db():
    """Helper for testing isolated states"""
    global _id_counter
    _mock_endpoints_db.clear()
    _id_counter = 1