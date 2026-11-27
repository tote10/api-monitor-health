"""Poll the monitor service and check a locally configured list of private URLs."""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import httpx


def validated_url(value: str) -> httpx.URL:
    url = httpx.URL(value)
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
        raise ValueError("URLs must be HTTP(S) with no embedded credentials")
    return url


async def observe(client: httpx.AsyncClient, target: httpx.URL | None, timeout: int) -> dict:
    if target is None:
        return {"failure": "unconfigured"}
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout):
            request = httpx.Request("GET", target, extensions={"timeout": httpx.Timeout(timeout).as_dict()})
            response = await client.send(request, stream=True, follow_redirects=False)
            try:
                return {"status_code": response.status_code,
                        "response_time_ms": round((time.perf_counter() - started) * 1000)}
            finally:
                await response.aclose()
    except (TimeoutError, httpx.TimeoutException):
        return {"failure": "timeout"}
    except (httpx.HTTPError, OSError):
        return {"failure": "connectivity"}


async def agent_loop(base: str, agent_id: int, token: str, targets: dict[str, httpx.URL], poll_seconds: int):
    endpoint = f"{base.rstrip('/')}/api/v1/agents/{agent_id}"
    async with (
        httpx.AsyncClient(trust_env=False, follow_redirects=False) as controller,
        httpx.AsyncClient(trust_env=False, follow_redirects=False,
                          limits=httpx.Limits(max_keepalive_connections=0)) as target_client,
    ):
        async def message(path: str, payload=None):
            return await controller.post(endpoint + path, headers={"Authorization": "Bearer " + token},
                                     json=payload, timeout=10)

        async def run_job(job: dict):
            outcome = await observe(target_client, targets.get(job["target"]), job["timeout_seconds"])
            try:
                response = await message(f"/results/{job['monitor_id']}", {"token": job["token"], **outcome})
                if response.status_code not in {204, 409}:
                    print(f"Agent result rejected (HTTP {response.status_code})", flush=True)
            except httpx.HTTPError:
                print("Agent could not submit a result; the check lease will expire.", flush=True)

        while True:
            try:
                response = await message("/poll")
                if response.status_code == 401:
                    raise SystemExit("Agent credential was rejected")
                response.raise_for_status()
                await asyncio.gather(*(run_job(job) for job in response.json()))
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                print("Agent could not poll the controller; retrying.", flush=True)
            await asyncio.sleep(poll_seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--agent-id", type=int, required=True)
    parser.add_argument("--targets", type=Path, required=True, help="JSON object mapping aliases to local URLs")
    parser.add_argument("--poll-seconds", type=int, choices=range(2, 31), default=10)
    parser.add_argument("--allow-local-http-controller", action="store_true")
    args = parser.parse_args()
    token = os.getenv("MONITOR_AGENT_TOKEN")
    if not token:
        parser.error("Set MONITOR_AGENT_TOKEN")
    try:
        controller = validated_url(args.controller)
        if controller.path not in {"", "/"} or controller.query or controller.fragment:
            raise ValueError("Pass a controller origin")
        if controller.scheme == "http" and not (
            args.allow_local_http_controller and controller.host in {"127.0.0.1", "::1"}
        ):
            raise ValueError("Use an HTTPS controller")
        config = json.loads(args.targets.read_text())
        if not isinstance(config, dict) or len(config) > 200:
            raise ValueError("Expected an object with at most 200 aliases")
        targets = {alias: validated_url(value) for alias, value in config.items()}
    except (ValueError, TypeError, OSError, httpx.InvalidURL):
        parser.error("Invalid controller or local target configuration")
    asyncio.run(agent_loop(str(controller), args.agent_id, token, targets, args.poll_seconds))


if __name__ == "__main__":
    main()
