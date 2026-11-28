# API Health Monitor

A self-hosted HTTP monitoring service with a web dashboard. Register the URLs you
care about, choose how often to check them, and the background scheduler records
every result so you can see status, uptime, response times, and failures.

- **Dashboard** at `/` with service cards, recent check bars, latency sparklines, and active incidents.
- **Scheduler** checks active endpoints when their interval elapses.
- **History** stores every check with status code, response time, and error detail.
- **Incidents** group consecutive failed checks and show the first recorded
  failure, the recovery check, and the observed duration.
- **Alerts** send outage and recovery notifications by email or webhook. Verified email recipients can choose monitors and opt into a weekly digest.
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

Use `docker compose down` to stop the stack and retain its data. The Compose
stack starts one API worker and uses PostgreSQL for scheduler and delivery claims.

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
workspace. Outstanding invites now create private accounts. The optional global
email and webhook destinations serve the shared workspace. Verified recipients can subscribe to monitors in their own workspace. On an existing
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

Apply the latest migration with `alembic upgrade head`, set the SMTP and base URL
options in the app folder’s `.env`, and restart the app. Docker Compose applies
migrations when the API starts.

| Setting | Purpose |
| --- | --- |
| `PUBLIC_BASE_URL` | Browser-facing app URL such as `https://monitor.example.com`. Required for recipient verification, public subscriptions, and email incident links. |
| `SMTP_HOST`, `SMTP_FROM` | SMTP host and sender address. Leave the host blank to disable email. |
| `SMTP_PORT` | Defaults to `587`. Use the port supplied by your mail provider. |
| `SMTP_SECURITY` | Defaults to `starttls`. Use `tls` for implicit TLS or `plain` for local capture. |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | Set both if the SMTP server requires sign-in. |
| `ALERT_EMAIL_TO` | Optional legacy destination for all shared workspace monitors. |
| `ALERT_WEBHOOK_URL` | Optional public HTTP(S) URL for JSON webhook delivery from shared workspace monitors. |

Open **Account → Email notifications** to add an address. Open the verification
email and confirm the address on the linked page. In settings, select the monitors
that should send alerts, optionally enable the weekly digest, and click **Save
preferences**. Use **Send test email** and **Refresh delivery status** to check
SMTP acceptance. Digests cover the previous Monday through Sunday in UTC and
report check-based uptime and incidents opened for each selected service.
Services without checks are reported as unknown.

Alert jobs are created when an incident opens or resolves at its configured
check thresholds. The same incident event queues at most one email per address.
Outage messages include the detection time and a standardized failure reason.
Recovery messages include the resolution time and observed duration from the
first failure. Both use the application's incident link. The incident timeline
records failed checks, notification attempts, acknowledgement, and recovery.

The queue retries temporary failures up to five attempts with backoff. A permanent
SMTP rejection ends delivery immediately. An SMTP acceptance records that the
server accepted the message for delivery. Inbox placement and later bounces are
outside the SMTP submission response. If the connection drops before acceptance
can be recorded, retries reuse the same Message-ID. The app retains completed
delivery records for 30 days while alert processing is enabled.

Webhooks do not follow redirects. Their JSON includes `event` (`endpoint.down`
or `endpoint.recovered`), `endpoint_id`, `endpoint_name`, `check_id`,
`checked_at`, `status_code`, and `delivery_id`. Receivers can use the delivery
ID to recognize retries.

### Capture emails in development

Set these values in the app's `.env` file for Docker Compose.

```dotenv
PUBLIC_BASE_URL=http://127.0.0.1:8000
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_FROM=monitor@example.test
SMTP_SECURITY=plain
SMTP_USERNAME=
SMTP_PASSWORD=
ALERT_EMAIL_TO=
```

From `backend/api-health-monitor`, run `docker compose --profile mail-capture up
--build --wait` and open the Mailpit inbox at <http://127.0.0.1:8025>. If you
run the app on the host, start just Mailpit with
`docker compose --profile mail-capture up -d mailpit` and use
`SMTP_HOST=127.0.0.1`. Both Mailpit ports bind to loopback. Use a sample recipient
such as `ops@example.test` and open verification, test, outage, and recovery
messages from the inbox. [Mailpit Docker documentation](https://mailpit.axllent.org/docs/install/docker/)

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

## Portfolio demo

From `backend/api-health-monitor`, start the app with the demo enabled. The demo
can run on its own with an in-memory app database and the scheduler disabled.

```bash
DATABASE_URL=sqlite:// MONITORING_ENABLED=false DEMO_ENABLED=true \
  .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/demo>. Click **Simulate outage**, then click it again
to record another failed check for the same incident. The inbox should still
contain one message. Click **Restore service** to resolve the incident and
capture the recovery email. Use the **Email** and **Format** selectors to read
both versions. The service card and timeline update after each check.

Each browser session gets its own temporary monitor, incident history, and
capture inbox. The fixed demo target runs in process and the demo uses an
in-memory mail capture with a fixed example address. Sessions expire after 30
minutes or when the worker restarts. Run the demo with one app worker so requests
reach the same session store. In Docker Compose, set `DEMO_ENABLED=true` in
`.env` and restart the API.

Run `pytest -q tests/test_email_experience.py` to verify the full alert flow
against a loopback SMTP receiver and check demo isolation.

## Local outage and recovery demo

Run this against a fresh local database and never enable the loopback exception
in a deployed environment. In one shell, from `backend/api-health-monitor`:

```bash
python scripts/demo_target.py
```

In another shell, activate the project environment, then configure and launch
the monitor:

```bash
export DATABASE_URL=sqlite:///./demo-monitor.db
export ENVIRONMENT=development ALLOW_LOOPBACK_TARGETS=true
export ALERT_WEBHOOK_URL=http://127.0.0.1:8765/webhook
export PUBLIC_BASE_URL=http://127.0.0.1:8000
alembic upgrade head
python -m app.create_user admin --role admin
uvicorn main:app
```

Sign in and add `http://127.0.0.1:8765/health` with an interval of 10 seconds,
a timeout of 1 second, and an open and recovery threshold of 2 checks in
**Advanced monitoring settings**. Let one healthy check complete, then run:

```bash
curl -X POST http://127.0.0.1:8765/mode/down
```

Two failed checks open one incident and queue one outage notification. Leave the
target down for another check to confirm it keeps the same incident. Set the
target back to `ok` through `POST /mode/ok` and wait for two passing checks to see
recovery. `POST /mode/slow` simulates a timeout with the settings above.
`GET http://127.0.0.1:8765/events` shows received webhook events. The target
deduplicates webhook retries using the `Idempotency-Key` header. Schedule a
maintenance window from the monitor card to see alerts suppressed at transition
time while checks continue. Use **Pause** to suspend scheduled checks.

### Check configuration and results

Existing monitors keep their method, status, and scheduling behavior through
migration defaults. The default consecutive failure and recovery thresholds
are both one. Set them higher per monitor if you want to filter transient
failures. Request options support headers, a string body, and a text containment
assertion. Generate a Fernet key with
`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
and supply it as `MONITOR_CONFIG_KEY` through the environment. Keep it stable
across workers and restarts and back it up separately from the database.
Request options are encrypted and write-only through the API. Put credentials
in request options rather than in monitor URLs. Redirects are revalidated.
Requests that follow redirects drop configured headers and bodies on an origin
change. Non-GET or HEAD requests do not follow redirects.

A slow HTTP response can still be available. Timeouts, connectivity errors,
unexpected status codes and failed assertions count as failed checks. Uptime is
successful recorded checks divided by all recorded checks in the selected range.
Missing observations are excluded and a range without observations is unknown.
Maintenance checks count toward availability. Their incident transitions are
recorded and the related notifications are suppressed. Percentiles use
nearest-rank measurements from checks that received an HTTP response. An active
monitor becomes stale after its interval, timeout and two scheduler polls have
elapsed without an observation.

Use `GET /api/v1/endpoints/{id}/incident-records` for incidents that follow the
configured open and recovery thresholds. The older `/incidents` route still
returns failure runs from retained check history for compatibility.

Webhook delivery is bounded and retries may repeat a send if a receiver accepted
it before the worker recorded success. Receivers can deduplicate on the stable
delivery ID. Server-wide email and webhook settings deliver shared workspace
events only. Authorized operators can see delivery outcomes at
`GET /api/v1/endpoints/{id}/deliveries`.

### Heartbeats

Choose **Add heartbeat**, enter a name, the expected cadence in seconds, and a
grace period. For a daily backup, enter 86400 seconds and a 900-second grace
period. Copy the token when it is shown and store it with the job's secrets.
After a successful job run, make an authenticated POST to the check-in URL:

```bash
curl --fail-with-body -X POST \
  -H "Authorization: Bearer $HEARTBEAT_TOKEN" \
  "http://127.0.0.1:8000/api/v1/heartbeats/$HEARTBEAT_ID/ping"
```

For a quick demo, use a 10-second cadence and 5-second grace. Wait for the
first missed check to open an incident. Send a valid check-in to resolve it.
Use **Edit** on the heartbeat card to rotate or revoke its credential.
Pausing a heartbeat stops automatic missing-check detection. Valid job
check-ins can still be recorded. Use HTTPS for real jobs.

### Multi-step checks

Keep the local demo target running, generate and export `MONITOR_CONFIG_KEY`,
and restart the monitor. Add an HTTP monitor with URL
`http://127.0.0.1:8765/workflow/login`, set a timeout for the entire run, and
paste this in **Request options (JSON)**:

```json
{
  "steps": [
    {
      "url": "http://127.0.0.1:8765/workflow/login",
      "method": "POST",
      "timeout_seconds": 3,
      "extract": {"session": "data.token"}
    },
    {
      "url": "http://127.0.0.1:8765/workflow/profile",
      "timeout_seconds": 3,
      "headers": {"Authorization": "Bearer {{session}}"},
      "contains": "account_active"
    },
    {
      "url": "http://127.0.0.1:8765/workflow/finish",
      "timeout_seconds": 2,
      "expected_status_codes": [200, 204]
    }
  ]
}
```

Use **Check now** once in `ok` mode, then set `POST /mode/down` and check
again. The second request receives HTTP 503, stops the workflow, and the
check history shows **Step 2**. Extraction accepts dot-separated JSON keys and array
indexes, with scalar values only. Placeholder substitution is supported in
headers, bodies and text assertions. Each workflow has up to eight steps and
each asserted or extracted response is capped at 128 KiB of uncompressed data.
Variables live only in the current run. Dashboard and API results include the
failed step and a generic reason without storing the raw response or variables.

### Deployments, diagnostics, reliability, and reports

- Send an authenticated `POST /api/v1/endpoints/{id}/deployments` with
  `{"label":"Release 1.2","revision":"abc123"}`. The optional `deployed_at`
  field accepts a timezone-aware timestamp. The history chart shows markers
  in its displayed period. Incident details show deployments on the same
  monitor from one hour before the first failure until recovery or the present.
  The timestamps show when events occurred. Operators can record a confirmed
  cause separately.
- Open **Diagnostics** in the admin account menu to view overdue checks,
  estimated missed checks, observed scheduler workers, and pending or failed
  alerts. Monitoring reports total duration. DNS, connect, TLS handshake, and
  time to first byte are marked unavailable.
- Set an availability objective and rolling window in **Advanced monitoring
  settings**. For a 99.9% objective, the error budget is 0.001 multiplied by
  the number of recorded checks, minus failed checks. For example, three
  recorded checks with one failure against a 90% objective leave a budget of
  −0.7 checks. Maintenance checks count, missing observations and unconfigured
  agent targets are excluded, and an empty window is unknown.
- **History** provides check and incident CSV exports. In an incident, open
  **Incident report** to edit the confirmed root cause and follow-up actions.
  The report also includes detection, acknowledgement, recovery and notes.
- Assign the same **Service group** to related monitors to link incidents
  that open within 30 minutes of each other. Grouping preserves each
  incident's own timeline. Tags are searchable and can also drive alerts.

### Alert routing and audit

In the account menu, open **Alert routing & audit** to add webhook, Slack, or
Discord destinations. The destination URL is encrypted using
`MONITOR_CONFIG_KEY`. Filters can match a specific monitor, service group, tag,
and severity. All supplied filters must match. **Send test** queues a test
message for the selected service. Reopen the panel to see its delivery outcome.
Set an escalation delay for critical incidents to send one additional message
when the incident remains open and unacknowledged. Acknowledgement or recovery
cancels an escalation before it is sent. A send already in progress can still
reach the destination.

Monitor, routing, maintenance, account, agent and public-status changes have
audit entries showing the actor, action, subject and timestamp. Credential
values are omitted. Server-wide SMTP and webhook settings apply to the shared
workspace. Use workspace alert routes for private workspaces.

### Status pages, TLS and history retention

Give a monitor a **Public service name** and publish **Public status page**
from the account menu. Share the generated page link. Published services show
their current health, scheduled maintenance times and any explicitly written
**Public incident update**. When SMTP and `PUBLIC_BASE_URL` are configured,
visitors can subscribe to an individual published service. Subscribers confirm
by email and can use the unsubscribe link in any update. These emails use the
public service name, incident timing, and a link to the public service status.
The public snapshot omits internal monitor names and URLs, private notes, raw
results, and credentials. Unpublish the page to stop serving it and suspend
subscription deliveries.

A successful TLS handshake can expose the leaf certificate's expiration.
Set `TLS_EXPIRY_WARNING_DAYS` (default 14, zero disables) to notify once for
an observed expiration per destination. Checks still verify TLS normally.
The history dialog shows the observed expiration if the connection exposes
it. **History retention** can override the server's existing
`CHECK_HISTORY_RETENTION_DAYS` policy per monitor. Blank inherits the server
setting and zero retains history indefinitely. A shorter retention setting can
reduce the observations available to longer SLO windows.

### Private agents

From **Private agents**, register an agent and copy its ID and one-time token.
On the machine inside the private network, prepare a local JSON file mapping
approved aliases to URLs:

```json
{"internal-health":"http://127.0.0.1:8765/health"}
```

Run the agent with the project environment installed:

```bash
export MONITOR_AGENT_TOKEN=YOUR_AGENT_TOKEN
python scripts/private_agent.py --controller https://monitor.example.com \
  --agent-id YOUR_AGENT_ID --targets ./private-targets.json
```

For a local demo with the controller on `http://127.0.0.1:8000`, add
`--allow-local-http-controller` and run `scripts/demo_target.py`. In the
dashboard, add a private monitor to the agent with target alias
`internal-health`. The controller supplies aliases only. Each agent can run
only GET checks against its local allowlist, does not follow redirects, and
returns status, latency or a failure classification. Local URLs stay on the
agent. Stopping the agent for 90 seconds shows **Agent offline** and makes
monitor health unknown without opening a service incident. A reachable agent
observing a failing service can open an incident normally. Agents and their
assigned monitors are isolated to their workspace.

### Deployment and delivery notes

The hosted application runs on Railway. The Docker startup script runs
`alembic upgrade head` before binding to the service's `PORT`. The image
health check uses the same port. For the hosted service, keep the existing
PostgreSQL connection and set a stable `MONITOR_CONFIG_KEY` for encrypted
request settings and alert destinations. Set `PUBLIC_BASE_URL` to the service's
public origin to include incident links in notifications.

Use PostgreSQL for concurrent production workers and apply migrations before
starting the new app version. SQLite remains supported for local evaluation.
Run `alembic upgrade head` against the intended database after backing it up.
Migration revisions are additive and intentionally reject downgrades that
would discard new records. Enable HTTPS and secure cookies in production.
Keep `ALLOW_LOOPBACK_TARGETS` disabled outside the local demo.
