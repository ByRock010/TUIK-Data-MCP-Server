"""Issue #102 transport prototype. The ASGI app and its identities exist only in tests."""

import asyncio
import base64
import hashlib
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, McpError
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import BlobResourceContents

from tuik_mcp.artifacts import ArtifactInput, ArtifactStore, PendingFile, uri
from tuik_mcp.clients.base import HttpClient
from tuik_mcp.clients.tuik_portal import TuikPortal
from tuik_mcp.config import Settings
from tuik_mcp.models.common import now
from tuik_mcp.server import App, TuikMCP, artifact_file, get_tuik_data
from tuik_mcp.services.catalog import CatalogService
from tuik_mcp.services.files import FileStore
from tuik_mcp.services.query import QueryService

FIXTURES = Path(__file__).with_name("fixtures")
ORIGIN = "https://pilot.example.test"
RESOURCE = f"{ORIGIN}/mcp"
ISSUER = "https://identity.example.test/"
FLOW = "flow:DF_ALTINSIZ_DTH+V1.0"
SCOPE = "tuik:read"

pytestmark = pytest.mark.skipif(
    "subject" not in AccessToken.model_fields
    or "validate_token_resource" not in AuthSettings.model_fields,
    reason="Hosted research prototype needs SDK subject and resource validation support",
)


class FixtureVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        claims = {
            "alice-token": ("alice", RESOURCE, [SCOPE], None),
            "bob-token": ("bob", RESOURCE, [SCOPE], None),
            "expired-token": ("alice", RESOURCE, [SCOPE], 1),
            "other-audience-token": ("alice", f"{ORIGIN}/other", [SCOPE], None),
            "no-scope-token": ("alice", RESOURCE, [], None),
        }.get(token)
        if claims is None:
            return None
        subject, audience, scopes, expiry = claims
        return AccessToken(
            token=token,
            client_id="same-test-client",
            subject=subject,
            scopes=scopes,
            expires_at=expiry,
            resource=audience,
        )


@asynccontextmanager
async def hosted(tmp_path):
    requests = []
    csv = (FIXTURES / "data.csv").read_bytes()

    def upstream(request):
        assert "authorization" not in request.headers
        requests.append(request.url.path)
        if request.url.path == "/api/tr/dataflows":
            return httpx.Response(200, content=(FIXTURES / "flows.json").read_bytes())
        if request.url.path.endswith("/file/csv"):
            return httpx.Response(200, content=csv, headers={"content-type": "text/csv"})
        raise AssertionError(f"Unexpected upstream request: {request.url}")

    settings = Settings(attempts=1, min_interval_seconds=0, download_interval_seconds=0)
    http = HttpClient(settings, httpx.MockTransport(upstream))
    files = FileStore(TuikPortal(http))
    catalogs = {owner: CatalogService(http) for owner in ("alice", "bob")}
    for owner, catalog in catalogs.items():
        catalog.artifacts = ArtifactStore(settings, root=tmp_path / owner)
        catalog.files = files
        catalog.queries = QueryService(files, settings)

    @asynccontextmanager
    async def lifespan(_server):
        # The auth context is per request. The services survive HTTP requests.
        access = get_access_token()
        assert access is not None and access.subject in catalogs
        yield App(catalogs[access.subject])

    mcp = TuikMCP(
        "TÜİK HTTP prototype",
        stateless_http=True,
        json_response=True,
        lifespan=lifespan,
        token_verifier=FixtureVerifier(),
        auth=AuthSettings(
            issuer_url=ISSUER,
            resource_server_url=RESOURCE,
            required_scopes=[SCOPE],
            validate_token_resource=True,
        ),
        transport_security=TransportSecuritySettings(
            allowed_hosts=["pilot.example.test"], allowed_origins=[ORIGIN]
        ),
    )
    mcp.add_tool(get_tuik_data, structured_output=False)
    mcp.resource("tuik-artifact://{artifact_id}/{name}")(artifact_file)
    app = mcp.streamable_http_app()
    try:
        async with app.router.lifespan_context(app):
            yield httpx.ASGITransport(app=app), catalogs, requests
    finally:
        for catalog in catalogs.values():
            await catalog.close()
        await http.close()


async def test_hosted_http_authorization_and_discovery(tmp_path):
    async with hosted(tmp_path) as (transport, _, upstream):
        headers = {"accept": "application/json, text/event-stream"}
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "fixture", "version": "1"},
            },
        }
        async with httpx.AsyncClient(
            transport=transport, base_url=ORIGIN, headers=headers
        ) as client:
            response = await client.post("/mcp", json=initialize)
            assert response.status_code == 401
            metadata_url = f"{ORIGIN}/.well-known/oauth-protected-resource/mcp"
            assert f'resource_metadata="{metadata_url}"' in response.headers["www-authenticate"]
            metadata = (await client.get(metadata_url)).raise_for_status().json()
            assert metadata["resource"] == RESOURCE
            assert metadata["authorization_servers"] == [ISSUER]
            assert metadata["scopes_supported"] == [SCOPE]

            for token, status in [
                ("unknown-token", 401),
                ("expired-token", 401),
                ("other-audience-token", 401),
                ("no-scope-token", 403),
            ]:
                response = await client.post(
                    "/mcp", json=initialize, headers={"authorization": f"Bearer {token}"}
                )
                assert response.status_code == status

            response = await client.post(
                "/mcp",
                json=initialize,
                headers={
                    "authorization": "Bearer alice-token",
                    "origin": "https://other.example.test",
                },
            )
            assert response.status_code == 403
        assert upstream == []


async def test_hosted_http_isolates_cursors_and_binary_artifacts(tmp_path):
    async with hosted(tmp_path) as (transport, catalogs, upstream):
        resource = await catalogs["alice"].resource(FLOW)
        csv = (FIXTURES / "data.csv").read_bytes()
        binary = b"\x00\xfffixture artifact\n"
        bundle = await catalogs["alice"].artifacts.create(
            kind="fixture",
            title="Türkçe",
            citation=resource.source_url,
            inputs=[
                ArtifactInput(
                    resource_id=FLOW,
                    source_url=resource.source_url,
                    retrieved_at=now(),
                    sha256=hashlib.sha256(csv).hexdigest(),
                    size=len(csv),
                    retention_note="The CSV is retained in tests/fixtures/data.csv.",
                )
            ],
            files=[
                PendingFile(
                    name="report.bin",
                    role="output",
                    mime_type="application/octet-stream",
                    content=binary,
                )
            ],
        )
        artifact_url = uri(bundle.artifact_id, "report.bin")

        async with AsyncExitStack() as stack:
            sessions = []
            for owner in ("alice", "bob"):
                client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        transport=transport,
                        headers={"authorization": f"Bearer {owner}-token"},
                    )
                )
                read, write, session_id = await stack.enter_async_context(
                    streamable_http_client(RESOURCE, http_client=client)
                )
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                assert session_id() is None
                assert [tool.name for tool in (await session.list_tools()).tools] == [
                    "get_tuik_data"
                ]
                sessions.append(session)
            alice, bob = sessions
            templates = (await alice.list_resource_templates()).resourceTemplates
            assert [template.uriTemplate for template in templates] == [
                "tuik-artifact://{artifact_id}/{name}"
            ]
            args = {"resource_id": FLOW, "query": {"columns": ["Gözlem Değeri"]}, "limit": 1}
            results = await asyncio.gather(*(s.call_tool("get_tuik_data", args) for s in sessions))
            first, second = (result.structuredContent for result in results)
            assert first["rows"] == second["rows"] == [{"Gözlem Değeri": "98,4622068329301"}]
            assert (
                first["snapshot_id"]
                == second["snapshot_id"]
                == "sha256:" + hashlib.sha256(csv).hexdigest()
            )
            assert first["next_cursor"] and first["next_cursor"] != second["next_cursor"]
            continuation = {"resource_id": FLOW, "cursor": first["next_cursor"], "limit": 1}
            own = await alice.call_tool("get_tuik_data", continuation)
            assert own.structuredContent["rows"] == [{"Gözlem Değeri": "101,664854746884"}]
            other = await bob.call_tool("get_tuik_data", continuation)
            assert other.structuredContent["code"] == "invalid_cursor"
            assert "rows" not in other.structuredContent

            (content,) = (await alice.read_resource(artifact_url)).contents
            assert isinstance(content, BlobResourceContents)
            assert content.mimeType == "application/octet-stream"
            assert base64.b64decode(content.blob) == binary
            with pytest.raises(McpError, match="not_found"):
                await bob.read_resource(artifact_url)
        assert sum(path.endswith("/file/csv") for path in upstream) == 1
