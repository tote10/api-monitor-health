import asyncio
from datetime import datetime, timedelta, timezone
from threading import Thread

from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import settings
from app.db.models import AlertDelivery, CheckResult
from app.services import alert_service, monitor_service
from scripts.demo_target import DemoServer


def test_scheduler_outage_recovery_maintenance_and_pause(client, session_factory, monkeypatch):
    with DemoServer(("127.0.0.1", 0)) as demo:
        thread = Thread(target=demo.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{demo.server_port}"
            monkeypatch.setattr(settings, "ALLOW_LOOPBACK_TARGETS", True)
            monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", SecretStr(base + "/webhook"))
            now = datetime.now(timezone.utc)
            monkeypatch.setattr(monitor_service, "_utcnow", lambda: now)
            endpoint = client.post("/api/v1/endpoints", json={
                "name": "Fixture", "url": base + "/health", "interval_seconds": 10,
                "timeout_seconds": 1, "failure_threshold": 2, "recovery_threshold": 2,
            })
            assert endpoint.status_code == 201, endpoint.text
            path = f"/api/v1/endpoints/{endpoint.json()['id']}"

            async def competing_polls():
                return sum(await asyncio.gather(
                    monitor_service.run_due_checks(session_factory), monitor_service.run_due_checks(session_factory),
                ))

            def tick(mode, expected=1):
                nonlocal now
                now += timedelta(seconds=10)
                demo.mode = mode
                assert asyncio.run(competing_polls()) == expected
                asyncio.run(alert_service.deliver_pending(session_factory))
                return client.get(path + "/incident-records").json()

            assert tick("ok") == []
            assert client.get(path + "/status").json()["is_up"] is True
            overview = client.get("/api/v1/endpoints/operations/overview")
            assert overview.status_code == 200, overview.text
            assert overview.json()["monitors"][0]["status"]["is_up"] is True
            assert len(overview.json()["trends"]) == 6
            assert b"operations-list" in client.get("/", headers={"Accept": "text/html"}).content
            assert tick("down") == []
            assert tick("ok") == []
            assert tick("down") == []
            outage, = tick("down")
            assert outage["recovered_at"] is None
            assert [event["event"] for event in demo.events.values()] == ["endpoint.down"]
            assert tick("down")[0]["id"] == outage["id"]
            assert tick("ok")[0]["recovered_at"] is None
            resolved, = tick("ok")
            assert resolved["id"] == outage["id"] and resolved["recovered_at"]
            assert [event["event"] for event in demo.events.values()] == ["endpoint.down", "endpoint.recovered"]

            window = client.post(path + "/maintenance", json={
                "title": "Demo deployment", "starts_at": now.isoformat(),
                "ends_at": (now + timedelta(minutes=2)).isoformat(),
            })
            assert window.status_code == 201
            tick("down")
            assert len(tick("down")) == 2
            assert len(demo.events) == 2
            assert tick("slow")[0]["recovered_at"] is None
            timeout = client.get(path + "/checks?limit=1").json()[0]
            assert timeout["failure_kind"] == "timeout"

            assert client.patch(path, json={"is_active": False}).status_code == 200
            tick("ok", expected=0)
            with session_factory() as db:
                results = list(db.scalars(select(CheckResult)))
                deliveries = list(db.scalars(select(AlertDelivery)))
                assert len(results) == 11
                assert [(row.state, row.attempts) for row in deliveries] == [("sent", 1), ("sent", 1)]
            assert client.patch(path, json={"is_active": True}).status_code == 200
            tick("ok")
        finally:
            demo.shutdown()
            thread.join()


def test_missed_heartbeat_recovers_and_revocation_applies(client, session_factory, monkeypatch):
    from app.db.models import Endpoint

    response = client.post("/api/v1/heartbeats", json={
        "name": "Backup", "interval_seconds": 86400, "heartbeat_grace_seconds": 900,
    })
    assert response.status_code == 201, response.text
    data = response.json()
    monitor = data["endpoint"]
    token = data["token"]
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        endpoint = db.get(Endpoint, monitor["id"])
        assert endpoint.heartbeat_token_hash != token
        endpoint.created_at = now - timedelta(days=1, minutes=16)
        db.commit()
    monkeypatch.setattr(monitor_service, "_utcnow", lambda: now)
    path = f"/api/v1/endpoints/{monitor['id']}"
    assert asyncio.run(monitor_service.run_due_checks(session_factory)) == 1
    assert asyncio.run(monitor_service.run_due_checks(session_factory)) == 0
    incident, = client.get(path + "/incident-records").json()
    assert incident["recovered_at"] is None
    assert client.get(path + "/checks").json()[0]["failure_kind"] == "missed_heartbeat"
    heartbeat = f"/api/v1/heartbeats/{monitor['id']}"
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post(heartbeat + "/ping", headers=headers).status_code == 204
    recovery, = client.get(path + "/incident-records").json()
    assert recovery["id"] == incident["id"] and recovery["recovered_at"]
    assert client.get(path).json()["last_heartbeat_at"]
    assert client.post(heartbeat + "/revoke").status_code == 204
    assert client.post(heartbeat + "/ping", headers=headers).status_code == 401


def test_encrypted_workflow_fails_at_step_two(client, session_factory, monkeypatch, caplog):
    from unittest.mock import AsyncMock

    import httpx
    from cryptography.fernet import Fernet

    from app.db.models import Endpoint
    from app.services import monitor_targets

    marker = "sensitive-token-123"
    observed_paths = []

    def handler(request):
        observed_paths.append(request.url.path)
        if request.url.path == "/authenticate":
            assert request.headers["x-api-key"] == "user-configured-secret"
            return httpx.Response(200, json={"token": marker})
        assert request.headers["authorization"] == f"Bearer {marker}"
        return httpx.Response(200, text=f"private response {marker}")

    monkeypatch.setattr(settings, "MONITOR_CONFIG_KEY", SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(monitor_targets, "resolve_public_addresses", AsyncMock(return_value=["1.1.1.1"]))
    monkeypatch.setattr(monitor_service, "create_http_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    base = "https://fixture.example"
    created = client.post("/api/v1/endpoints", json={
        "name": "Workflow", "url": base, "request_options": {"steps": [
            {"method": "POST", "url": base + "/authenticate", "headers": {"x-api-key": "user-configured-secret"},
             "extract": {"token": "token"}},
            {"url": base + "/profile", "headers": {"Authorization": "Bearer {{token}}"}, "contains": "expected"},
            {"url": base + "/never"},
        ]},
    })
    assert created.status_code == 201, created.text
    endpoint_id = created.json()["id"]
    assert "user-configured-secret" not in created.text
    with session_factory() as db:
        assert "user-configured-secret" not in db.get(Endpoint, endpoint_id).request_config
    assert asyncio.run(monitor_service.run_due_checks(session_factory)) == 1
    path = f"/api/v1/endpoints/{endpoint_id}"
    result, = client.get(path + "/checks").json()
    assert result["failed_step"] == 2 and result["error"].startswith("Step 2:")
    assert observed_paths == ["/authenticate", "/profile"]
    incidents = client.get("/api/v1/endpoints/operations/incidents").json()
    assert incidents[0]["first_error"].startswith("Step 2:")
    assert marker not in str(result) + str(incidents) + caplog.text


def test_reliability_calculation_and_service_deployment_timeline(client, session_factory, monkeypatch):
    from app.db.models import Incident

    start = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(monitor_service, "_utcnow", lambda: start)
    endpoints = [
        client.post("/api/v1/endpoints", json={
            "url": f"https://service-{i}.example", "name": f"Service {i}",
            "slo_target_percentage": 90, "slo_window_hours": 24,
        }).json()
        for i in (1, 2)
    ]
    endpoint = endpoints[0]["id"]
    path = f"/api/v1/endpoints/{endpoint}"
    with session_factory() as db:
        for minutes, success, latency in [(2, True, 10), (7, False, 60), (44, True, 30)]:
            db.add(CheckResult(endpoint_id=endpoint, is_success=success, status_code=200 if success else 503,
                               response_time_ms=latency, checked_at=start - timedelta(minutes=minutes)))
        outage = Incident(endpoint_id=endpoint, first_failed_at=start - timedelta(minutes=7),
                          opened_at=start - timedelta(minutes=7), recovered_at=start - timedelta(minutes=2))
        db.add(outage)
        db.commit()
        outage_id = outage.id
    assert client.post(path + "/maintenance", json={
        "title": "Scheduled", "starts_at": (start - timedelta(minutes=10)).isoformat(),
        "ends_at": (start - timedelta(minutes=6)).isoformat(),
    }).status_code == 201
    metrics = client.get(path + "/reliability").json()
    assert metrics["maintenance_checks"] == 1
    assert metrics["total_checks"] == 3 and metrics["uptime_percentage"] == 66.6667
    assert metrics["p50_ms"] == 30 and metrics["p95_ms"] == 60
    assert metrics["target_met"] is False and metrics["remaining_error_budget"] == -0.7
    assert client.get(path + "/checks.csv").text.count("\n") == 4
    for item in endpoints:
        assert client.post(f"/api/v1/endpoints/{item['id']}/deployments", json={
            "label": item["name"], "deployed_at": (start - timedelta(minutes=8)).isoformat(),
        }).status_code == 201
    report = f"{path}/incident-records/{outage_id}"
    assert [row["label"] for row in client.get(report + "/deployments").json()] == ["Service 1"]
    assert client.patch(report + "/report", json={"root_cause": "Confirmed cause"}).status_code == 200
    assert client.get(report + "/report").json()["root_cause"] == "Confirmed cause"


def test_acknowledgement_cancels_exactly_one_escalation(client, session_factory, monkeypatch):
    from unittest.mock import AsyncMock

    from cryptography.fernet import Fernet

    from app.db.models import Endpoint

    monkeypatch.setattr(settings, "MONITOR_CONFIG_KEY", SecretStr(Fernet.generate_key().decode()))
    route = client.post("/api/v1/integrations", json={
        "name": "Critical alerts", "kind": "discord", "destination": "https://chat.example/hook/secret",
        "tag": "backup", "severity": "critical", "escalation_seconds": 10,
    })
    assert route.status_code == 201, route.text
    assert "secret" not in route.text
    endpoint = client.post("/api/v1/endpoints", json={
        "name": "Critical backup", "url": "https://backup.example",
        "severity": "critical", "tags": ["backup"],
    }).json()
    monitor_id = endpoint["id"]
    path = f"/api/v1/endpoints/{monitor_id}"
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        for seconds in (5, 4, 3):
            alert_service.save_check_results(db, [CheckResult(
                endpoint_id=monitor_id, checked_at=now - timedelta(seconds=seconds),
                is_success=False, error="Fixture failure",
            )])
        db.commit()
        assert db.get(Endpoint, monitor_id).severity == "critical"
        assert [delivery.payload["event"] for delivery in db.scalars(
            select(AlertDelivery).order_by(AlertDelivery.id)
        )] == ["endpoint.down", "endpoint.escalated"]

    sender = AsyncMock()
    monkeypatch.setattr(alert_service, "send_webhook", sender)
    asyncio.run(alert_service.deliver_pending(session_factory))
    incident, = client.get(path + "/incident-records").json()
    ack = path + f"/incident-records/{incident['id']}/acknowledge"
    acknowledged = client.post(ack).json()["acknowledged_at"]
    assert acknowledged and client.post(ack).json()["acknowledged_at"] == acknowledged
    asyncio.run(alert_service.deliver_pending(session_factory))
    assert sender.await_count == 1
    assert "allowed_mentions" in sender.await_args.args[1]
    deliveries = client.get(path + "/deliveries").json()
    assert {item["event"]: item["state"] for item in deliveries} == {
        "endpoint.down": "sent", "endpoint.escalated": "canceled",
    }


def test_private_agent_offline_is_distinct_and_other_workspace_cannot_use_it(client, session_factory):
    from app.db.models import PrivateAgent
    from tests.conftest import TEST_PASSWORD

    response = client.post("/api/v1/agents", json={"name": "Private network"})
    assert response.status_code == 201, response.text
    agent = response.json()
    prefix = f"/api/v1/agents/{agent['id']}"
    token = {"Authorization": "Bearer " + agent["token"]}
    monitor = client.post(prefix + "/monitors", json={
        "name": "Internal API", "target": "internal-api", "interval_seconds": 10,
    })
    assert monitor.status_code == 201, monitor.text
    monitor_id = monitor.json()["id"]
    path = f"/api/v1/endpoints/{monitor_id}"
    assert client.get(path + "/status").json()["agent_offline"] is True
    assert asyncio.run(monitor_service.run_due_checks(session_factory)) == 0

    jobs = client.post(prefix + "/poll", headers=token)
    assert jobs.status_code == 200, jobs.text
    job, = jobs.json()
    assert job["target"] == "internal-api" and "url" not in job
    assert client.post(prefix + "/poll", headers=token).json() == []
    result_url = prefix + f"/results/{monitor_id}"
    data = {"token": job["token"], "status_code": 503, "response_time_ms": 12}
    assert client.post(result_url, headers=token, json=data).status_code == 204
    assert client.post(result_url, headers=token, json=data).status_code == 409
    online_status = client.get(path + "/status").json()
    assert online_status["agent_offline"] is False and online_status["is_up"] is False
    with session_factory() as db:
        row = db.get(PrivateAgent, agent["id"])
        row.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        db.commit()
    offline_status = client.get(path + "/status").json()
    assert offline_status["agent_offline"] is True and offline_status["is_up"] is None
    assert len(client.get(path + "/incident-records").json()) == 1

    invite = client.post("/auth/invites").json()["token"]
    assert client.post("/auth/register", json={
        "username": "other-operator", "password": TEST_PASSWORD, "invite_token": invite,
    }).status_code == 201
    assert client.post("/auth/login", json={"username": "other-operator", "password": TEST_PASSWORD}).status_code == 200
    assert client.get("/api/v1/agents").json() == []
    assert client.post(prefix + "/monitors", json={"name": "Other service", "target": "other"}).status_code == 404
    assert client.post(prefix + "/revoke").status_code == 404
    assert client.get(path + "/status").status_code == 404


def test_public_page_only_publishes_explicit_service_data(client, session_factory):
    from app.db.models import Incident

    private_url = "https://internal-name.example/?secret=a-value"
    monitor = client.post("/api/v1/endpoints", json={
        "name": "Internal monitor name", "url": private_url, "public_label": "Public API",
    }).json()
    client.post("/api/v1/endpoints", json={"name": "Hidden service", "url": "https://hidden.example"})
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        db.add(Incident(
            endpoint_id=monitor["id"], first_failed_at=now, opened_at=now,
            first_error="Private failure", notes=[{"text": "Private note"}],
            public_update="We are investigating.",
        ))
        db.commit()
    result = client.put("/operations/status-settings", json={"title": "Public demo", "enabled": True})
    assert result.status_code == 200, result.text
    url = result.json()["path"]
    client.post("/auth/logout")
    assert client.get(url).status_code == 200
    snapshot = client.get(url + "/snapshot")
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["services"] == [{"name": "Public API", "state": "unknown"}]
    assert snapshot.json()["incidents"][0]["message"] == "We are investigating."
    for secret in ("a-value", "Hidden service", "Private failure", "Private note", "Internal monitor name"):
        assert secret not in snapshot.text


def test_expiring_certificate_only_queues_one_warning(client, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", SecretStr("https://receiver.example"))
    endpoint = client.post("/api/v1/endpoints", json={
        "name": "TLS server", "url": "https://tls.example",
    }).json()
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        for offset in (1, 2):
            alert_service.save_check_results(db, [CheckResult(
                endpoint_id=endpoint["id"], is_success=True, status_code=200,
                checked_at=now + timedelta(seconds=offset), certificate_expires_at=now + timedelta(days=3),
            )])
        db.commit()
        delivery, = list(db.scalars(select(AlertDelivery)))
        assert delivery.payload["event"] == "endpoint.certificate_expiring"


def test_reads_tls_expiry_from_a_verified_local_peer(client, monkeypatch, tmp_path):
    import ssl
    from ipaddress import ip_address

    import httpx
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    now = datetime.now(timezone.utc).replace(microsecond=0)
    expiry = now + timedelta(days=3)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Local TLS fixture")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(expiry)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_file, key_file)
    trust = ssl.create_default_context(cafile=str(cert_file))
    monkeypatch.setattr(settings, "ALLOW_LOOPBACK_TARGETS", True)
    monkeypatch.setattr(monitor_service, "create_http_client",
                        lambda: httpx.AsyncClient(verify=trust, trust_env=False))
    with DemoServer(("127.0.0.1", 0)) as demo:
        demo.socket = tls.wrap_socket(demo.socket, server_side=True)
        thread = Thread(target=demo.serve_forever, daemon=True)
        thread.start()
        try:
            created = client.post("/api/v1/endpoints", json={
                "name": "TLS test fixture", "url": f"https://127.0.0.1:{demo.server_port}/health",
            })
            assert created.status_code == 201
            check = client.post(f"/api/v1/endpoints/{created.json()['id']}/check")
            assert check.status_code == 201, check.text
            assert check.json()["is_success"] is True
            assert check.json()["certificate_expires_at"] == expiry.isoformat().replace("+00:00", "Z")
        finally:
            demo.shutdown()
            thread.join()
