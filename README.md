# API Health Monitor

A self-hosted HTTP monitoring service with a web dashboard. Register the URLs you
care about, choose how often to check them, and the background scheduler records
every result so you can see status, uptime, response times, incidents and
failures. Accounts, roles and alert channels are built in.

- **Dashboard** at `/` for adding, editing, pausing, checking, and deleting endpoints.
- **Accounts** with `admin` and `viewer` roles; viewers can read, admins can change.
- **Scheduler** checks active endpoints when their interval elapses.
- **History** stores every check with status code, response time, and error detail.
- **Charts** show uptime and response time for 24 hours, 7 days or 30 days, plus incidents.
- **Alerts** go to email addresses and webhooks (Slack/Discord compatible) on down and recovery.
- **SSRF protection** blocks private, loopback and reserved targets, including redirect hops.
- **Retention** prunes old results automatically (30 days by default).
- **REST API** with interactive docs at `/docs`.
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

cp .env.example .env

alembic upgrade head
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000> for the dashboard and <http://127.0.0.1:8000/docs>
for the API documentation. The first visit asks you to create an account; that
account becomes the administrator. Data is stored in `api_health_monitor.db` next
to the app and is ignored by git.

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

Docker with the Compose plugin is the only requirement. Compose sets its own
PostgreSQL `DATABASE_URL`, so the SQLite settings in `.env.example` do not apply
and the local SQLite file is not used.

```bash
cd backend/api-health-monitor
docker compose up --build
```

Open <http://localhost:8000> for the dashboard and create the first account. The
first start builds the image, creates the PostgreSQL volume, and runs migrations
automatically.

```bash
docker compose down      # stop, keeping data
docker compose down -v   # stop and delete the database volume
```

Optional environment overrides:

```bash
APP_PORT=9000 POSTGRES_PASSWORD=change-me docker compose up --build
SMTP_HOST=smtp.example.com SMTP_USERNAME=bot SMTP_PASSWORD=secret \
  docker compose up --build
```

The API container runs one worker because the scheduler lives inside the API
process. Do not increase `--workers` without moving scheduling to an external
system.

Endpoints added locally live in the SQLite file, so they are not carried over to
the Docker Compose PostgreSQL volume.

## Accounts and roles

- The first account created through the dashboard or `POST /api/v1/auth/register`
  becomes the `admin`.
- Later self-service sign-ups become `viewer` accounts. Viewers can see
  endpoints, status, history and charts but cannot change anything.
- Admins can add accounts, promote or demote people, reset passwords and
  deactivate accounts under `/api/v1/auth/users`. The last active administrator
  cannot be demoted or deactivated.
- Sign-in sets an HTTP-only session cookie (7 days by default). Logging out
  revokes the session in the database. Browser requests can also use
  `Authorization: Bearer <session token>`; the token is the same value as the
  cookie.

Set `AUTH_ENABLED=false` only on a trusted network (for example behind an
authenticating proxy): it turns off login and role checks entirely. When the
dashboard is served over HTTPS, set `SESSION_COOKIE_SECURE=true` so the cookie
is only sent over TLS. Password resets and deactivations revoke existing
sessions.

## Alerts

Admins can add alert channels from the dashboard or `/api/v1/channels`:

- **Email** sends through the SMTP server configured with `SMTP_HOST`,
  `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` and `SMTP_USE_TLS`.
- **Webhook** posts JSON to any URL, including Slack and Discord webhooks. The
  payload contains `text` and `content` fields plus structured `endpoint` data,
  so the same message renders in both services.

Messages are sent when an endpoint changes state: the first failure after a
healthy check, and the recovery afterwards. Repeated failures do not re-send
while the endpoint stays down, and a new outage inside
`ALERT_COOLDOWN_MINUTES` (15 by default) is suppressed to avoid flapping noise.
Set the cooldown to `0` to alert on every state change. Each endpoint also has a
**Send alerts** toggle, and each channel can be disabled without deleting it.

Use **Send test** next to a channel to check the configuration; delivery errors
are stored on the channel and shown in the dashboard.

## Security

- Targets, webhook URLs and every redirect hop are checked before a request is
  made. Schemes other than `http`/`https`, URLs with embedded credentials, and
  hosts that resolve to private, loopback, link-local or reserved addresses are
  rejected.
- The guard resolves the hostname and then lets the HTTP client connect
  normally, so a hostile DNS server could answer differently between those two
  lookups (DNS rebinding). For multi-tenant or adversarial use, also restrict
  outbound traffic at the network or proxy layer.
- Set `SSRF_PROTECTION_ENABLED=false` or `ALLOW_PRIVATE_TARGETS=true` to monitor
  internal services on a trusted network. Webhooks are validated the same way.
- Passwords are stored as salted scrypt hashes. Sessions are stored as SHA-256
  token digests. Cookies are HTTP-only and `SameSite=Lax`.
- The dashboard has no CSRF tokens; `SameSite=Lax` plus JSON-only request bodies
  cover the common cross-site cases. Put the service behind TLS and an
  authenticating reverse proxy for internet-facing deployments.

## Configuration

Environment variables are read from the process environment and from `.env`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | *(required)* | SQLAlchemy URL. `.env.example` uses `sqlite:///./api_health_monitor.db`; for PostgreSQL use `postgresql+psycopg://user:pass@host:5432/db`. |
| `MONITORING_ENABLED` | `true` | Start the background scheduler. Manual checks still work when disabled. |
| `SCHEDULER_POLL_SECONDS` | `5` | How often the scheduler looks for due endpoints. This is not the per-endpoint interval. |
| `CHECK_HISTORY_RETENTION_DAYS` | `30` | Delete check results older than this. Set to `0` to keep history forever. |
| `CHECK_HISTORY_CLEANUP_INTERVAL_SECONDS` | `3600` | How often retention runs. |
| `AUTH_ENABLED` | `true` | Require sign-in and enforce roles. Disable only on a trusted network. |
| `ALLOW_REGISTRATION` | `true` | Allow self-service viewer sign-ups after the first admin exists. |
| `SESSION_COOKIE_NAME` | `ahm_session` | Name of the session cookie. |
| `SESSION_TTL_HOURS` | `168` | Session lifetime in hours. |
| `SESSION_COOKIE_SECURE` | `false` | Send the session cookie only over HTTPS. |
| `SSRF_PROTECTION_ENABLED` | `true` | Reject targets that resolve to private or reserved addresses. |
| `ALLOW_PRIVATE_TARGETS` | `false` | Permit private addresses when SSRF protection is on. |
| `ALERT_COOLDOWN_MINUTES` | `15` | Minimum gap between outage alerts for one endpoint. `0` disables the cooldown. |
| `SMTP_HOST` | *(empty)* | SMTP server for email alerts. Email channels report an error until this is set. |
| `SMTP_PORT` | `587` | SMTP port. |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | *(empty)* | SMTP credentials; login is skipped when the username is empty. |
| `SMTP_FROM` | `api-health-monitor@localhost` | Sender address for alert emails. |
| `SMTP_USE_TLS` | `true` | Use STARTTLS for SMTP. |
| `ENVIRONMENT` | `development` | Label returned by `/health`. |
| `PROJECT_NAME` | `API Health Monitor` | Application title. |
| `API_V1_PREFIX` | `/api/v1` | Prefix for the REST API. |

## How monitoring works

1. An endpoint is registered with a URL, interval (10–3600 seconds), timeout
   (1–60 seconds), expected status code, and active flag.
2. The scheduler wakes every `SCHEDULER_POLL_SECONDS` and checks active
   endpoints whose last result is older than their interval.
3. Each check sends one GET request and records success or failure. A status
   that differs from the expected one counts as a failure, as does a timeout or
   connection error. Redirects are followed up to five hops, and every hop is
   validated first.
4. Results appear in the dashboard, the status endpoint, the history endpoint
   and the statistics endpoint. Deleting an endpoint also deletes its history.
5. When an endpoint goes down or recovers, enabled alert channels are notified.
6. Old results are pruned in the background according to the retention setting.

URLs are unique, ignoring a trailing slash. A manual check runs immediately and
does not change the endpoint's schedule settings.

## API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Dashboard for browsers, status JSON for API clients. |
| `GET` | `/health` | Liveness information. |
| `GET` | `/ready` | Readiness; returns `503` when the database is unreachable. |
| `GET` | `/api/v1/auth/session` | Authentication state for the current request. |
| `POST` | `/api/v1/auth/register` | Create an account and sign in. |
| `POST` | `/api/v1/auth/login` | Sign in with email and password. |
| `POST` | `/api/v1/auth/logout` | Revoke the current session. |
| `GET` | `/api/v1/auth/users` | List accounts (admin). |
| `POST` | `/api/v1/auth/users` | Create an account with a role (admin). |
| `PATCH` | `/api/v1/auth/users/{id}` | Change role, reset password or deactivate (admin). |
| `GET` | `/api/v1/channels` | List alert channels (admin). |
| `POST` | `/api/v1/channels` | Add an email or webhook channel (admin). |
| `PATCH` | `/api/v1/channels/{id}` | Update or enable/disable a channel (admin). |
| `DELETE` | `/api/v1/channels/{id}` | Delete a channel (admin). |
| `POST` | `/api/v1/channels/{id}/test` | Send a test alert (admin). |
| `POST` | `/api/v1/endpoints` | Register an endpoint (admin). |
| `GET` | `/api/v1/endpoints` | List endpoints. |
| `GET` | `/api/v1/endpoints/{id}` | Get one endpoint. |
| `PATCH` | `/api/v1/endpoints/{id}` | Update configuration or pause/resume (admin). |
| `DELETE` | `/api/v1/endpoints/{id}` | Delete an endpoint and its history (admin). |
| `POST` | `/api/v1/endpoints/{id}/check` | Run a check now (admin). |
| `GET` | `/api/v1/endpoints/{id}/checks?limit=50` | Recent check results, newest first. |
| `GET` | `/api/v1/endpoints/{id}/status` | Latest result and uptime summary. |
| `GET` | `/api/v1/endpoints/{id}/stats?hours=24` | Uptime and response-time buckets plus incidents for 1–720 hours. |

Sign in and reuse the cookie (or pass the token as a bearer token):

```bash
curl -c cookies.txt -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "you@example.com", "password": "your-password"}'

curl -b cookies.txt -X POST http://localhost:8000/api/v1/endpoints \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Production API",
    "url": "https://api.example.com/health",
    "interval_seconds": 60,
    "timeout_seconds": 5,
    "expected_status_code": 200,
    "is_active": true,
    "alerts_enabled": true
  }'
```

## Production notes

- Run one API worker, or move scheduling to an external system before scaling out.
- Serve the dashboard over HTTPS, set `SESSION_COOKIE_SECURE=true`, and put the
  service behind an authenticating reverse proxy for extra protection.
- Back up PostgreSQL, and keep `CHECK_HISTORY_RETENTION_DAYS` sized to your
  available storage.
- Use `/ready` for load balancer and container health checks.
- Apply migrations with `alembic upgrade head` before starting a new release;
  the Docker image does this automatically.
