import asyncio
import json
import time
from urllib.parse import urljoin

import httpx

from tuik_mcp.config import USER_AGENT, Settings
from tuik_mcp.utils.normalization import official_url


class SourceError(Exception):
    def __init__(self, code: str, message: str, source_url: str):
        super().__init__(message)
        self.code, self.source_url = code, source_url


class HttpClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._client = httpx.AsyncClient(
            timeout=settings.timeout_seconds,
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest"},
        )
        self._lock = asyncio.Lock()
        self._next_request = 0.0

    async def close(self) -> None:
        await self._client.aclose()

    async def _pace(self) -> None:
        async with self._lock:
            await asyncio.sleep(max(0, self._next_request - time.monotonic()))
            self._next_request = time.monotonic() + self.settings.min_interval_seconds

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        official_url(url, fetch=True)
        original_url = url
        for attempt in range(self.settings.attempts):
            current_url = original_url
            retry_after = 0.0
            try:
                for redirect in range(4):
                    await self._pace()
                    async with self._client.stream(method, current_url, **kwargs) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            target = urljoin(
                                str(response.url), response.headers.get("location", "")
                            )
                            try:
                                official_url(target, fetch=True)
                            except ValueError as exc:
                                raise SourceError(
                                    "unsafe_redirect", str(exc), original_url
                                ) from exc
                            if redirect == 3 or not response.headers.get("location"):
                                raise SourceError(
                                    "redirect_error", "Invalid redirect", original_url
                                )
                            # Only preserve read-only GET redirects; never replay a POST elsewhere.
                            if method != "GET":
                                raise SourceError(
                                    "redirect_error", "Unexpected POST redirect", original_url
                                )
                            current_url = target
                            continue
                        if response.status_code == 429 or response.status_code >= 500:
                            try:
                                retry_after = min(
                                    30, max(0, float(response.headers.get("retry-after", 0)))
                                )
                            except ValueError:
                                retry_after = 0
                            response.raise_for_status()
                        if response.is_error:
                            raise SourceError(
                                "upstream_http",
                                f"TÜİK returned HTTP {response.status_code}",
                                original_url,
                            )
                        size = 0
                        chunks = []
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > self.settings.max_response_bytes:
                                raise SourceError(
                                    "response_too_large",
                                    "TÜİK response exceeds the configured size limit",
                                    original_url,
                                )
                            chunks.append(chunk)
                        headers = dict(response.headers)
                        headers.pop("content-encoding", None)
                        headers.pop("content-length", None)
                        return httpx.Response(
                            response.status_code,
                            headers=headers,
                            content=b"".join(chunks),
                            request=response.request,
                        )
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if attempt + 1 == self.settings.attempts:
                    code = (
                        "upstream_timeout"
                        if isinstance(exc, httpx.TimeoutException)
                        else "upstream_unavailable"
                    )
                    raise SourceError(
                        code, "TÜİK request failed after bounded retries", original_url
                    ) from exc
                await asyncio.sleep(max(retry_after, 0.5 * 2**attempt))
        raise SourceError("upstream_unavailable", "TÜİK request failed", original_url)

    async def json(self, method: str, url: str, **kwargs):
        response = await self.request(method, url, **kwargs)
        try:
            return response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SourceError("malformed_response", "TÜİK returned invalid JSON", url) from exc
