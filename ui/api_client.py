"""Self-API client for the Python UI layer.

The UI is a Backend-for-Frontend: fragment endpoints render HTML by consuming
the very same JSON API that external clients use (/api/admin/*, /api/agent/*,
/v1/*). This guarantees exact behavioral parity with the gateway (sync on
create, probing, masking, aggregation) with zero duplicated logic.
"""
from __future__ import annotations

from typing import Any

import httpx

from nova.config import PORT


class ApiError(Exception):
    """Raised when the JSON API answers with an error — `.message` is user-facing."""

    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.message = message
        self.status = status


class NovaApiClient:
    """Async httpx client pointed at this very server (127.0.0.1:$PORT)."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # read=150: provider tests can legitimately take ~25s (engine cap).
            self._client = httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{PORT}",
                timeout=httpx.Timeout(connect=10.0, read=150.0, write=60.0, pool=120.0),
                trust_env=False,
            )
        return self._client

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        data: Any = None,
        timeout: float | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {}
        if json is not None:
            kwargs["json"] = json
        if params:
            kwargs["params"] = {k: v for k, v in params.items() if v is not None}
        if files is not None:
            kwargs["files"] = files
        if data is not None:
            kwargs["data"] = data
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            res = await self.client.request(method, path, **kwargs)
        except httpx.HTTPError as err:
            raise ApiError(f"API request failed: {err.__class__.__name__}") from err
        if res.status_code >= 400:
            try:
                body = res.json()
            except ValueError:
                body = {}
            raise ApiError(str(body.get("error") or f"HTTP {res.status_code}"), res.status_code)
        if res.status_code == 204 or not res.content:
            return None
        return res.json()

    async def get(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> Any:
        return await self.request("POST", path, **kw)

    async def patch(self, path: str, **kw: Any) -> Any:
        return await self.request("PATCH", path, **kw)

    async def delete(self, path: str, **kw: Any) -> Any:
        return await self.request("DELETE", path, **kw)


api = NovaApiClient()
