# API Health Monitor

A self-hosted HTTP monitoring service with a web dashboard. Register the URLs you
care about, choose how often to check them, and the background scheduler records
every result so you can see status, uptime, response times, and failures.

- **Dashboard** at `/` for adding, editing, pausing, checking, and deleting endpoints.
- **Scheduler** checks active endpoints when their interval elapses.
- **History** stores every check with status code, response time, and error detail.
- **Incidents** group consecutive failed checks and show the first recorded
  failure, the recovery check, and the observed duration.
- **Alerts** can send email or webhook notifications when a monitored endpoint goes down or recovers.
- **Retention** prunes old results automatically (30 days by default).
- **Authentication** with administrator, private member, and read-only viewer accounts.
- **REST API** with interactive docs at `/docs` for signed-in administrators.
- **SQLite or PostgreSQL** storage through SQLAlchemy and Alembic migrations.
- **Docker Compose** setup with a database healthcheck and migrations on startup.

## Quick start without Docker

Only Python 3.11 or newer is required. The default `.env.example` uses
SQLite, so no database server is needed.

```bash
cd backend/api-health-monitor

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

test -f .env || cp .env.example .env

alembic upgrade head
python -m app.create_user admin --role admin
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000> for the dashboard and <http://127.0.0.1:8000/docs>
for the API documentation. Data is stored in `api_health_monitor.db` next to the
app and is ignored by git.

Run the test suite with:

```bash
pytest -q
```

The tests use an in-memory SQLite database and do not touch your configured
database.

To use PostgreSQL instead of SQLite, point `DATABASE_URL` in `.env` at your
database (see `.env.example`) and run `alembic upgrade head` again. For example:

```bash
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/api_health_monitor"
```

## Run with Docker

Install Docker with the Compose plugin. From the app folder, copy
`.env.example` to `.env` if you are setting it up for the first time. Add a
value for `POSTGRES_PASSWORD` in that file. You can generate one with
`openssl rand -hex 24`. If you already have a Compose database volume, use
its current password. Updating the environment variable alone cannot change the
password stored in an existing database.

```bash
cd backend/api-health-monitor
docker compose config --quiet
docker compose up --build --wait
docker compose exec api python -m app.create_user admin --role admin
```

Create the admin once, then open <http://127.0.0.1:8000/>. The stack starts
PostgreSQL, applies migrations and waits for the app to become ready. It
binds the dashboard to loopback by default. Set `APP_PORT=9000` in `.env` if
port 8000 is already in use. An existing SQLite installation keeps its data
in its own database.

Use `docker compose down` to stop the stack and retain its data. Run one API
worker because the scheduler and notification delivery loop are in that worker.

## Accounts and access

Account creation prompts for a password. Run the account creation command after
applying migrations, including when upgrading an existing installation. Use
`--role viewer` to create a read-only account.

- Administrators manage shared endpoints, invite members, and list or delete accounts in **Manage users**. Deleting an account revokes its sessions and removes its private endpoints and monitoring history. Administrators cannot delete their own accounts from the dashboard.
- Members manage endpoints and view results in their private workspace.
- Viewers can see shared endpoints, uptime, response times, and check history.
- The dashboard and monitoring API require sign-in. Swagger, ReDoc, and the OpenAPI schema require administrator access.
  `/health` and `/ready` remain public for service probes.

### Invite someone to register

Sign in as an administrator, open **Invite user**, and create a link. Send it
directly to the person you want to invite. They choose a username and
password, then get an empty private dashboard where they can manage their own
endpoints and see their results. Every workspace can monitor the same URL.
Each link expires after seven days and can create one account.

Existing command-line admin and viewer accounts keep access to the shared
workspace. Outstanding invites now create private accounts. The global
email and webhook destinations serve the shared workspace. On an existing
deployment, apply the latest database migration before inviting anyone.

API clients can send JSON containing `username` and `password` to
`POST /auth/login` and retain the session cookie for subsequent requests.
`GET /auth/me` returns the signed-in account and its role. Sessions expire
after 12 hours, and `POST /auth/logout` revokes the current session.

## Configuration

Environment variables are read from the process environment and from `.env`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | *(required)* | SQLAlchemy URL. `.env.example` uses `sqlite:///./api_health_monitor.db`; for PostgreSQL use `postgresql+psycopg://user:pass@host:5432/db`. |
| `AUTH_SECURE_COOKIE` | `false` | Set to `true` when serving over HTTPS to mark session cookies Secure. |
| `MONITORING_ENABLED` | `true` | Start the background scheduler. Manual checks still work when disabled. |
| `SCHEDULER_POLL_SECONDS` | `5` | How often the scheduler looks for due endpoints. This is not the per-endpoint interval. |
| `CHECK_HISTORY_RETENTION_DAYS` | `30` | Delete check results older than this. Set to `0` to keep history forever. |
| `CHECK_HISTORY_CLEANUP_INTERVAL_SECONDS` | `3600` | How often retention runs. |
| `ENVIRONMENT` | `development` | Label returned by `/health`. |
| `PROJECT_NAME` | `API Health Monitor` | Application title. |
| `API_V1_PREFIX` | `/api/v1` | Prefix for the REST API. |

## Outage and recovery alerts

Apply the latest migration with `alembic upgrade head`, configure one or both
destinations in the app folder’s `.env`, and restart the app. Docker Compose
applies the migration on startup and reads the same settings when launched
from the app folder.

| Setting | Purpose |
| --- | --- |
| `ALERT_WEBHOOK_URL` | Public HTTP or HTTPS URL that accepts a JSON POST. Leave blank to disable webhooks. Use HTTPS for destinations that accept it. |
| `SMTP_HOST` | SMTP server hostname. Leave blank to disable email. |
| `SMTP_FROM`, `ALERT_EMAIL_TO` | Sender and recipient email addresses. Both are required when email is enabled. |
| `SMTP_PORT` | Defaults to `587`. Use the port supplied by your mail provider. |
| `SMTP_SECURITY` | `starttls` by default. Use `tls` for implicit TLS, commonly on port 465, or `plain` for a local test receiver. |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | Optional SMTP credentials. Set both if your mail provider requires sign-in. |

A failed check queues a down alert when the previous check passed or the
endpoint has no prior result. The next successful check queues a recovery.
This works for manual and scheduled checks. Repeated checks with the same
result leave the alert queue as it was. Editing an endpoint’s expected status
can trigger an alert on its next check.

The app saves queued deliveries alongside their check result and retries a
failed delivery up to five total attempts with backoff. Delivery order is
preserved per endpoint and channel. Webhooks do not follow redirects. Their
JSON payload includes `event` (`endpoint.down` or `endpoint.recovered`),
`endpoint_id`, `endpoint_name`, `check_id`, `checked_at`, `status_code`,
and `delivery_id`. Receivers can use the delivery ID to recognize retries.
The app logs failed attempts and keeps completed delivery records for 30 days
while alert processing is enabled. Pending deliveries persist across restarts.

## How monitoring works

1. An endpoint is registered with a URL, interval (10–3600 seconds), timeout
   (1–60 seconds), expected status code, and active flag.
2. The scheduler wakes every `SCHEDULER_POLL_SECONDS` and checks active
   endpoints whose last result is older than their interval.
3. Each check uses GET and only connects to public HTTP or HTTPS targets.
   It validates DNS answers and follows redirects under the same rules, up to
   20 hops. The configured timeout covers the full check. A blocked target,
   unexpected status, timeout, or connection error counts as a failure.
4. Results appear in the dashboard, the status endpoint, and the history
   endpoint. Deleting an endpoint also deletes its history.
5. Old results are pruned in the background according to the retention setting.

URLs are unique, ignoring a trailing slash. The API rejects new or edited URLs
that contain credentials, local hostnames, or non-public IP literals. DNS names
are resolved during each check. Previously saved endpoints that violate the
policy can still be edited, paused, or deleted. Checks connect directly to
validated addresses and ignore proxy settings from the environment.

A manual check runs immediately and
does not change the endpoint's schedule settings.

## API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Dashboard for browsers, status JSON for API clients. |
| `GET` | `/health` | Liveness information. |
| `GET` | `/ready` | Readiness; returns `503` when the database is unreachable. |
| `POST` | `/api/v1/endpoints` | Register an endpoint. |
| `GET` | `/api/v1/endpoints` | List endpoints. |
| `GET` | `/api/v1/endpoints/{id}` | Get one endpoint. |
| `PATCH` | `/api/v1/endpoints/{id}` | Update configuration or pause/resume. |
| `DELETE` | `/api/v1/endpoints/{id}` | Delete an endpoint and its history. |
| `POST` | `/api/v1/endpoints/{id}/check` | Run a check now. |
| `GET` | `/api/v1/endpoints/{id}/checks?limit=50` | Recent check results, newest first. |
| `GET` | `/api/v1/endpoints/{id}/status` | Latest result and uptime summary. |
| `GET` | `/api/v1/endpoints/{id}/incidents?limit=20` | Incident timeline from retained checks, newest first. |

Example:

```bash
# Paste an administrator's login JSON when prompted, then press Ctrl-D.
curl --fail-with-body -c monitor.cookies http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' --data-binary @-

curl -b monitor.cookies -X POST http://localhost:8000/api/v1/endpoints \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Production API",
    "url": "https://api.example.com/health",
    "interval_seconds": 60,
    "timeout_seconds": 5,
    "expected_status_code": 200,
    "is_active": true
  }'

curl -b monitor.cookies -X POST http://localhost:8000/auth/logout
rm monitor.cookies
```

## Deploy on Northflank Sandbox

Create a project and an internal PostgreSQL add-on. Link your GitHub account,
then create a combined service from the branch containing this release.

| Service setting | Value |
| --- | --- |
| Build type | Dockerfile |
| Dockerfile path | `/backend/api-health-monitor/Dockerfile` |
| Build context | `/backend/api-health-monitor` |
| Service port | Public HTTP on `8000` |
| Replicas | One |
| Runtime environment | `ENVIRONMENT=production`, `DEBUG=false`, `AUTH_SECURE_COOKIE=true` |
| Database secret | `DATABASE_URL` using the add-on's internal connection details and `postgresql+psycopg://` as the scheme |
| Readiness check | HTTP `GET /ready` on port `8000` |

Use the add-on's TLS settings in the connection URL. If you assemble the URL
from separate credentials, URL-encode the password. Store it as a runtime
secret. The Docker startup command applies migrations automatically.

Once the deployment is ready, open its container shell and run
`python -m app.create_user admin --role admin`. Enter your password at the
prompt. Open the service's generated HTTPS address and sign in. In production,
use the default password length requirement. Keep the database add-on internal.

## Deploy with HTTPS

Run Compose on your chosen host with these settings in the app folder’s
private `.env`:

```dotenv
APP_BIND_ADDRESS=127.0.0.1
APP_PORT=8000
AUTH_SECURE_COOKIE=true
```

Set `POSTGRES_PASSWORD`, then start the stack and create the admin as above.
Put a reverse proxy on the same host and point your domain at it. For a host
running Caddy, a site block looks like this:

```caddyfile
monitor.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Replace the hostname with your own domain. The proxy must preserve the original
`Host` header so sign-in and dashboard updates pass origin checks. Keep the
application and database ports behind the proxy. Open your HTTPS domain to
sign in when secure cookies are enabled.

### Release checks

- Check `/ready` on your HTTPS domain.
- Sign in as admin. Add a public HTTPS endpoint you control, run **Check now**,
  and wait for a second check in **History**.
- If you enabled notifications, confirm that your intended receiver gets an
  outage and a recovery alert from a practice endpoint.
- Verify that a signed-out browser session is redirected to sign-in.
- Back up the database before upgrading an existing deployment.

GitHub Actions runs the tests, validates migrations on SQLite and PostgreSQL,
and smoke-tests the Docker stack on pushes and pull requests.

### Back up and restore PostgreSQL

Run these from the app folder, and keep the backup outside the repository:

```bash
docker compose exec -T db pg_dump -U postgres -Fc api_health_monitor > monitor-backup.dump
docker compose exec -T db pg_restore -l < monitor-backup.dump
```

To verify a restore in a separate database on the same instance:

```bash
docker compose exec -T db createdb -U postgres api_health_monitor_restore
docker compose exec -T db pg_restore --exit-on-error -U postgres -d api_health_monitor_restore < monitor-backup.dump
docker compose exec -T db psql -U postgres -d api_health_monitor_restore -c 'SELECT version_num FROM alembic_version;'
```

A backup contains application data and password hashes. Store it with the same
access restrictions as the database. For release troubleshooting, run
`docker compose ps` and `docker compose logs --tail=100 api`.
