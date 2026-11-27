import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime

import httpx
from pydantic import ValidationError

from app.api.schemas import RequestFields, RequestOptions
from app.db.models import Endpoint
from app.services.monitor_targets import ResponseTooLarge, UnsafeMonitorURL, fetch_public_url

VARIABLE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]{0,39})\}\}")


@dataclass
class Observation:
    status_code: int | None = None
    failure_kind: str | None = None
    error: str | None = None
    failed_step: int | None = None
    certificate_expires_at: datetime | None = None


def extract_values(response: httpx.Response, extraction: dict[str, str], variables: dict[str, str]) -> None:
    if not extraction:
        return
    try:
        data = json.loads(response.content)
        for name, path in extraction.items():
            value = data
            for key in path.split("."):
                value = value[int(key)] if isinstance(value, list) else value[key]
            if not isinstance(value, (str, int, bool, float)):
                raise ValueError
            rendered = value if isinstance(value, str) else json.dumps(value, allow_nan=False)
            if len(rendered) > 4096:
                raise ValueError
            variables[name] = rendered
    except (ValueError, KeyError, IndexError, TypeError, OverflowError, RecursionError):
        raise ValueError("JSON extraction did not yield a supported value") from None


def render_fields(fields: RequestFields, variables: dict[str, str]) -> RequestFields:
    def substitute(value: str) -> str:
        return VARIABLE.sub(lambda match: variables[match[1]], value)

    return RequestFields(
        headers={name: substitute(value) for name, value in fields.headers.items()},
        body=substitute(fields.body) if fields.body is not None else None,
        contains=substitute(fields.contains) if fields.contains is not None else None,
    )


async def observe(
    client: httpx.AsyncClient, url: str, timeout: int, method: str,
    fields: RequestFields, expected: list[int], extraction: dict[str, str] | None = None,
    variables: dict[str, str] | None = None,
) -> Observation:
    code = None
    try:
        if variables is not None:
            fields = render_fields(fields, variables)
        response = await fetch_public_url(
            client, url, timeout, method=method, headers=fields.headers, body=fields.body,
            read_body=bool(extraction) or fields.contains is not None,
        )
        code = response.status_code
        expiry = response.extensions.get("certificate_expires_at")
        if code not in expected:
            return Observation(code, "http_status", f"Expected status {', '.join(map(str, expected))}, got {code}", certificate_expires_at=expiry)
        if fields.contains is not None and fields.contains.encode() not in response.content:
            return Observation(code, "assertion", "Response assertion did not match", certificate_expires_at=expiry)
        if extraction:
            extract_values(response, extraction, variables)
        return Observation(code, certificate_expires_at=expiry)
    except (TimeoutError, httpx.TimeoutException):
        return Observation(code, "timeout", f"Check timed out after {timeout} seconds")
    except UnsafeMonitorURL as exc:
        return Observation(code, "destination", str(exc))
    except ResponseTooLarge as exc:
        return Observation(code, "assertion", str(exc))
    except (httpx.HTTPError, httpx.InvalidURL, OSError) as exc:
        return Observation(code, "connectivity", f"{type(exc).__name__}: Request failed")
    except (KeyError, ValidationError):
        return Observation(code, "configuration", "Workflow variable is missing or cannot be used in this request")
    except ValueError:
        return Observation(code, "assertion", "JSON extraction did not yield a supported value")


async def execute_http_check(client: httpx.AsyncClient, endpoint: Endpoint, options: RequestOptions) -> Observation:
    if not options.steps:
        return await observe(
            client, endpoint.url, endpoint.timeout_seconds, endpoint.method,
            options, endpoint.expected_status_codes or [endpoint.expected_status_code],
        )

    variables = {}
    number = 1
    earliest_expiry = None
    try:
        async with asyncio.timeout(endpoint.timeout_seconds):
            for number, step in enumerate(options.steps, 1):
                result = await observe(
                    client, str(step.url), step.timeout_seconds, step.method,
                    step, step.expected_status_codes, step.extract, variables,
                )
                expiry = result.certificate_expires_at
                if expiry and (earliest_expiry is None or expiry < earliest_expiry):
                    earliest_expiry = expiry
                if result.error:
                    result.certificate_expires_at = earliest_expiry
                    result.error = f"Step {number}: {result.error}"
                    result.failed_step = number
                    return result
    except TimeoutError:
        return Observation(None, "timeout", f"Step {number}: Workflow exceeded its total timeout", number)
    result.certificate_expires_at = earliest_expiry
    return result
