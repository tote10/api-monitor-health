import re
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent
ASSET_REFERENCE = re.compile(r'((?:href|src)="/static/)([\w.-]+\.(?:css|js))"')


class RevalidatedStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@lru_cache(maxsize=32)
def _asset_version(filename: str) -> str:
    return sha256((APP_DIR / "static" / filename).read_bytes()).hexdigest()[:16]


def page_response(filename: str, *, headers: dict[str, str] | None = None) -> HTMLResponse:
    html = (APP_DIR / "templates" / filename).read_text(encoding="utf-8")
    html = ASSET_REFERENCE.sub(
        lambda match: f'{match[1]}{match[2]}?v={_asset_version(match[2])}"',
        html,
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-store", **(headers or {})})
