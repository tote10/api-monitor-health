# API Health Monitor

A self-hosted HTTP monitoring service with a web dashboard. Register the URLs you
care about, choose how often to check them, and the background scheduler records
every result so you can see status, uptime, response times, and failures.

- **Dashboard** at `/` for adding, editing, pausing, checking, and deleting endpoints.
- **Scheduler** checks active endpoints when their interval elapses.
- **History** stores every check with status code, response time, and error detail.
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

Docker with the Compose plugin is the only requirement. Compose sets its own
PostgreSQL `DATABASE_URL`, so the SQLite settings in `.env.example` do not apply
and the local SQLite file is not used.

```bash
cd backend/api-health-monitor
docker compose up --build
```

Open <http://localhost:8000> for the dashboard. The first start builds the image,
creates the PostgreSQL volume, and runs migrations automatically.

```bash
docker compose down      # stop, keeping data
docker compose down -v   # stop and delete the database volume
```

Optional environment overrides:

```bash
APP_PORT=9000 POSTGRES_PASSWORD=change-me docker compose up --build
```

The API container runs one worker because the scheduler lives inside the API
process. Do not increase `--workers` without moving scheduling to an external
system.

Endpoints added locally live in the SQLite file, so they are not carried over to
the Docker Compose PostgreSQL volume.

## Configuration

Environment variables are read from the process environment and from `.env`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | *(required)* | SQLAlchemy URL. `.env.example` uses `sqlite:///./api_health_monitor.db`; for PostgreSQL use `postgresql+psycopg://user:pass@host:5432/db`. |
| `MONITORING_ENABLED` | `true` | Start the background scheduler. Manual checks still work when disabled. |
| `SCHEDULER_POLL_SECONDS` | `5` | How often the scheduler looks for due endpoints. This is not the per-endpoint interval. |
| `CHECK_HISTORY_RETENTION_DAYS` | `30` | Delete check results older than this. Set to `0` to keep history forever. |
| `CHECK_HISTORY_CLEANUP_INTERVAL_SECONDS` | `3600` | How often retention runs. |
| `ENVIRONMENT` | `development` | Label returned by `/health`. |
| `PROJECT_NAME` | `API Health Monitor` | Application title. |
| `API_V1_PREFIX` | `/api/v1` | Prefix for the REST API. |

## How monitoring works

1. An endpoint is registered with a URL, interval (10–3600 seconds), timeout
   (1–60 seconds), expected status code, and active flag.
2. The scheduler wakes every `SCHEDULER_POLL_SECONDS` and checks active
   endpoints whose last result is older than their interval.
3. Each check sends one GET request, follows redirects, and records success or
   failure. A status that differs from the expected one counts as a failure, as
   does a timeout or connection error.
4. Results appear in the dashboard, the status endpoint, and the history
   endpoint. Deleting an endpoint also deletes its history.
5. Old results are pruned in the background according to the retention setting.

URLs are unique, ignoring a trailing slash. A manual check runs immediately and
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

Example:

```bash
curl -X POST http://localhost:8000/api/v1/endpoints \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Production API",
    "url": "https://api.example.com/health",
    "interval_seconds": 60,
    "timeout_seconds": 5,
    "expected_status_code": 200,
    "is_active": true
  }'
```

## Production notes

- Run one API worker, or move scheduling to an external system before scaling out.
- Put the service behind TLS and an authentication proxy. It does not include
  user accounts or built-in authentication.
- Back up PostgreSQL, and keep `CHECK_HISTORY_RETENTION_DAYS` sized to your
  available storage.
- Use `/ready` for load balancer and container health checks.
- Apply migrations with `alembic upgrade head` before starting a new release;
  the Docker image does this automatically.
