"""Loopback-only Streamable HTTP for the invite-only TÜİK MCP pilot."""

import asyncio
import hashlib
import json
import logging
import os
import re
import stat
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from importlib.metadata import version
from pathlib import Path

import httpx
from mcp.server.fastmcp.exceptions import ResourceError, ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse

from tuik_mcp import server
from tuik_mcp.artifacts import ArtifactError, ArtifactStore
from tuik_mcp.cache import MemoryTTLCache
from tuik_mcp.clients.base import HttpClient
from tuik_mcp.clients.tuik_portal import TuikPortal
from tuik_mcp.config import Settings
from tuik_mcp.hosted_auth import (
    BEARER,
    READ,
    REPORTS,
    HostedSettings,
    IntrospectionVerifier,
    IssuerUnavailable,
)
from tuik_mcp.hosted_limits import Admission
from tuik_mcp.services.catalog import CatalogService
from tuik_mcp.services.files import FileStore, finish_worker

MIB = 1024 * 1024
MAX_REQUEST = 64 * 1024
MAX_RESPONSE = 128 * 1024
MAX_RESOURCE_RESPONSE = 12 * MIB
MAX_CONNECTIONS = 16
WORKSPACE_ID = re.compile(r"[a-f0-9]{64}")
PARSE_TOOLS = {"get_tuik_data", "inspect_tuik_data", "chart_tuik_data"}
DEADLINE = 30
METADATA = "/.well-known/oauth-protected-resource/mcp"
CALLER: ContextVar[str] = ContextVar("hosted_caller")
PILOT_TOOLS = (
    server.search_tuik,
    server.search_tuik_datasets,
    server.get_tuik_resource,
    server.get_tuik_data,
    server.inspect_tuik_data,
    server.chart_tuik_data,
    server.list_tuik_themes,
    server.search_tuik_classifications,
)


class PayloadLimit(Exception):
    pass


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError("Hosted storage must be a local private directory")
    info = path.stat()
    if os.name == "posix" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise OSError("Hosted storage must be owned by the worker and private")


def saving(arguments):
    # Also require scope for values that a tool validator may coerce to true.
    return isinstance(arguments, dict) and "save" in arguments and arguments["save"] is not False


class HostedMCP(server.TuikMCP):
    def __init__(self, admission: Admission, **kwargs):
        self.admission = admission
        self.active_saves = 0
        self.active_parses = 0
        super().__init__(**kwargs)

    @asynccontextmanager
    async def slot(self, error, *, save=False, parse=False):
        caller = CALLER.get()
        if (
            (save and self.active_saves >= 2)
            or (parse and self.active_parses >= 2)
            or not self.admission.start_call(caller)
        ):
            raise error("pilot_busy: retry after the active request finishes")
        self.active_saves += int(save)
        self.active_parses += int(parse)
        try:
            if parse and not self.admission.reserve_parse(caller):
                raise error("parse_limit: daily hosted parsing budget exhausted")
            yield
        finally:
            self.active_saves -= int(save)
            self.active_parses -= int(parse)
            self.admission.in_flight.remove(caller)

    async def call_tool(self, name, arguments):
        table_read = name == "get_tuik_data"
        continuation = table_read and bool(arguments.get("cursor"))
        if table_read and not continuation and arguments.get("query") is None:
            # HTTP table reads always use the full-scan budgets and cursor contract.
            arguments = {**arguments, "query": {}}
        async with self.slot(
            ToolError,
            save=name == "chart_tuik_data" and saving(arguments),
            parse=name in PARSE_TOOLS and not continuation,
        ):
            return await super().call_tool(name, arguments)

    async def read_resource(self, uri):
        async with self.slot(ResourceError):
            return await super().read_resource(uri)


class HostedBoundary:
    """Check origin, caller, scopes and wire bounds before the SDK handles the request."""

    def __init__(self, app, config: HostedSettings, verifier, admission: Admission):
        self.app = app
        self.config = config
        self.verifier = verifier
        self.admission = admission

    @staticmethod
    def answer(status: int, code: str, headers=None):
        return JSONResponse({"error": code}, status_code=status, headers=headers)

    def challenge(self, status: int, scope: str = READ):
        error = "invalid_token" if status == 401 else "insufficient_scope"
        detail = (
            f'Bearer error="{error}", resource_metadata="{self.config.origin}{METADATA}", '
            f'scope="{scope}"'
        )
        return self.answer(status, error, {"WWW-Authenticate": detail})

    def quota(self):
        return self.answer(429, "request_limit", {"Retry-After": "60"})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.monotonic()
        status = 0
        response_bytes = 0

        async def no_store(message):
            nonlocal status, response_bytes
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [(key, val) for key, val in message["headers"] if key != b"cache-control"]
                message = {
                    **message,
                    "headers": [*headers, (b"cache-control", b"private, no-store")],
                }
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
            await send(message)

        if self.admission.connections >= MAX_CONNECTIONS:
            await self.answer(503, "pilot_busy")(scope, receive, no_store)
            return
        self.admission.connections += 1
        try:
            request = Request(scope, receive)
            try:
                async with asyncio.timeout(DEADLINE):
                    response = await self.dispatch(request)
            except TimeoutError:
                response = self.answer(504, "request_timeout")
            except ClientDisconnect:
                return
            await response(scope, receive, no_store)
        finally:
            self.admission.connections -= 1
            if status:
                logging.getLogger(__name__).info(
                    "hosted_http status=%d response_bytes=%d latency_ms=%d",
                    status,
                    response_bytes,
                    int((time.monotonic() - started) * 1000),
                )

    async def dispatch(self, request: Request):
        host = request.headers.getlist("host")
        origins = request.headers.getlist("origin")
        if host != [self.config.authority] or origins not in ([], [self.config.origin]):
            return self.answer(403, "origin_or_host_rejected")
        if request.method == "GET" and request.url.path == METADATA:
            return JSONResponse(
                {
                    "resource": self.config.resource_url,
                    "authorization_servers": [self.config.issuer_url],
                    "scopes_supported": [READ, REPORTS],
                    "bearer_methods_supported": ["header"],
                }
            )
        if request.method == "GET" and request.url.path in ("/livez", "/readyz"):
            return JSONResponse({"status": "ok"})
        if request.url.path != "/mcp":
            return self.answer(404, "not_found")
        if not self.admission.pre_auth(request.client.host if request.client else "unknown"):
            return self.quota()
        supplied = request.headers.getlist("authorization")
        if len(supplied) != 1 or (token := BEARER.fullmatch(supplied[0])) is None:
            return self.challenge(401)
        try:
            identity = await self.verifier.verify(token[1])
        except IssuerUnavailable:
            return self.answer(503, "authorization_unavailable")
        if identity is None:
            return self.challenge(401)
        if not self.admission.request(identity.subject):
            return self.quota()
        if READ not in identity.scopes:
            return self.challenge(403)

        if request.method != "POST":
            return self.answer(405, "method_not_allowed", {"Allow": "POST"})
        try:
            body, method = await self.read_request(request)
        except PayloadLimit:
            return self.answer(413, "request_too_large")
        except (ValueError, RecursionError):
            return self.answer(400, "invalid_request")
        params = method.get("params")
        save = (
            method.get("method") == "tools/call"
            and isinstance(params, dict)
            and params.get("name") == "chart_tuik_data"
            and saving(params.get("arguments"))
        )
        resource = method.get("method") == "resources/read"
        if (save or resource) and REPORTS not in identity.scopes:
            return self.challenge(403, f"{READ} {REPORTS}")
        limit = MAX_RESOURCE_RESPONSE if resource else MAX_RESPONSE

        context = CALLER.set(identity.subject)
        try:
            return await self.run_sdk(request, body, limit)
        finally:
            CALLER.reset(context)

    @staticmethod
    async def read_request(request: Request):
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            raise ValueError("Unsupported content encoding")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_REQUEST:
                raise PayloadLimit
            body.extend(chunk)
        method = json.loads(body.decode("utf-8"))
        if not isinstance(method, dict):
            raise ValueError("Expected one JSON-RPC message")
        return bytes(body), method

    async def run_sdk(self, request: Request, body: bytes, limit: int):
        # This profile uses single JSON responses. Buffer up to the wire limit before
        # sending a response so oversize results cannot leak partial bodies.
        sent = False
        start = None
        response = bytearray()
        oversized = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body}
            return await request.receive()

        async def capture(message):
            nonlocal start, oversized
            if message["type"] == "http.response.start":
                start = message
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                if len(response) + len(chunk) > limit:
                    oversized = True
                if not oversized:
                    response.extend(chunk)

        # Do not pass credentials or caller-supplied identity headers into MCP.
        headers = [item for item in request.scope["headers"] if item[0] != b"authorization"]
        await self.app({**request.scope, "headers": headers}, receive, capture)
        if oversized:
            return self.answer(413, "response_too_large")

        async def replay(scope, receive, send):
            if start is not None:
                await send(start)
                await send({"type": "http.response.body", "body": bytes(response)})

        return replay


def create_hosted_app(
    config: HostedSettings,
    *,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
    issuer_transport: httpx.AsyncBaseTransport | None = None,
):
    """Create an isolated worker. The listening socket is owned by the entry point."""
    settings = Settings(
        opensearch_url=None,
        semantic_search=False,
        max_response_bytes=8 * MIB,
        parse_max_uncompressed_bytes=32 * MIB,
        parse_max_cells=100_000,
        query_max_cells=100_000,
        query_cache_bytes=2 * MIB,
        query_max_snapshot_bytes=2 * MIB,
        artifact_max_total_bytes=64 * MIB,
        artifact_max_file_bytes=8 * MIB,
        artifact_max_jobs=1,
        artifact_retain_sources=False,
    )
    admission = Admission()
    issuer_http = httpx.AsyncClient(
        transport=issuer_transport, timeout=5, trust_env=False, follow_redirects=False
    )
    upstream = HttpClient(settings, upstream_transport)
    files = FileStore(TuikPortal(upstream), cache_profiles=False)
    cache = MemoryTTLCache()
    dataset_lock = asyncio.Lock()
    catalogs: dict[str, CatalogService] = {}
    stores: dict[str, ArtifactStore] = {}

    @asynccontextmanager
    async def workspace(_mcp) -> AsyncIterator[server.App]:
        yield server.App(catalogs[CALLER.get()], inline_saved_images=False)

    mcp = HostedMCP(
        admission,
        name="TÜİK MCP local hosted pilot",
        stateless_http=True,
        json_response=True,
        lifespan=workspace,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[config.authority], allowed_origins=[config.origin]
        ),
    )
    for tool in PILOT_TOOLS:
        mcp.add_tool(tool, structured_output=False)
    mcp.resource("tuik-artifact://{artifact_id}/{name}")(server.artifact_file)
    app = mcp.streamable_http_app()
    sdk_lifespan = app.router.lifespan_context

    async def sweep_periodically():
        while True:
            await asyncio.sleep(3600)
            for store in stores.values():
                try:
                    await finish_worker(store.sweep)
                except (OSError, ArtifactError):
                    logging.getLogger(__name__).warning("hosted_storage_sweep_failed")

    @asynccontextmanager
    async def lifecycle(app):
        try:
            private_directory(config.artifact_dir)
            roster = {
                subject: hashlib.sha256(f"{config.issuer_url}\0{subject}".encode()).hexdigest()
                for subject in config.subjects
            }
            keys = set(roster.values())
            for entry in config.artifact_dir.iterdir():
                if WORKSPACE_ID.fullmatch(entry.name):
                    keys.add(entry.name)
                if len(keys) > 128:
                    raise OSError("Hosted storage has too many retained workspaces")
            per_workspace = settings.model_copy(
                update={"artifact_max_total_bytes": min(64 * MIB, 512 * MIB // len(keys))}
            )
            for key in sorted(keys):
                directory = config.artifact_dir / key
                private_directory(directory)
                store = ArtifactStore(per_workspace, root=directory, max_bundle_bytes=16 * MIB)
                await finish_worker(store.sweep)
                stores[key] = store
            for subject, key in roster.items():
                catalogs[subject] = CatalogService(
                    upstream,
                    files=files,
                    dataset_lock=dataset_lock,
                    dataset_cache=cache,
                    artifacts=stores[key],
                )
            async with sdk_lifespan(app):
                sweeper = asyncio.create_task(sweep_periodically())
                try:
                    yield
                finally:
                    sweeper.cancel()
                    with suppress(asyncio.CancelledError):
                        await sweeper
        finally:
            for catalog in catalogs.values():
                await catalog.close()
            await upstream.close()
            await issuer_http.aclose()

    app.router.lifespan_context = lifecycle
    app.add_middleware(
        HostedBoundary,
        config=config,
        verifier=IntrospectionVerifier(config, issuer_http),
        admission=admission,
    )
    return app


def main() -> None:
    import uvicorn

    if version("mcp") != "1.30.0":
        raise SystemExit("Install tuik-mcp[hosted] for the reviewed MCP SDK version.")
    try:
        config = HostedSettings.from_env()
    except (OSError, ValueError):
        raise SystemExit("Invalid hosted configuration. See docs/HOSTED_MCP.md.") from None
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name in ("httpx", "httpcore", "mcp"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    uvicorn.run(
        create_hosted_app(config),
        host="127.0.0.1",
        port=8000,
        workers=1,
        lifespan="on",
        proxy_headers=False,
        access_log=False,
        limit_concurrency=MAX_CONNECTIONS,
    )
