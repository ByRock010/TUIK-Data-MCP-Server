"""Exercise the hosted entry point against recorded sources and fake introspection."""

import asyncio
import base64
import json
import time
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from mcp import ClientSession, McpError
from mcp.client.streamable_http import streamable_http_client

from tuik_mcp.hosted import MAX_REQUEST, METADATA, create_hosted_app
from tuik_mcp.hosted_auth import READ, REPORTS, HostedSettings

FIXTURES = Path(__file__).with_name("fixtures")
ORIGIN = "https://pilot.example.test"
RESOURCE = f"{ORIGIN}/mcp"
ISSUER = "https://identity.example.test"
FLOW = "flow:DF_ALTINSIZ_DTH+V1.0"
ACCEPT = {"accept": "application/json, text/event-stream"}
QUERY = {"resource_id": FLOW, "query": {"columns": ["Gözlem Değeri"]}, "limit": 1}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "fixture-client", "version": "1"},
    },
}


def configuration(tmp_path, **overrides):
    values = {
        "resource_url": RESOURCE,
        "issuer_url": ISSUER,
        "introspection_url": f"{ISSUER}/introspect",
        "introspection_client_id": "server-client",
        "introspection_secret": "fixture-secret",
        "subjects": {"alice", "bob", "reader"},
        "client_ids": {"fixture-client"},
        "artifact_dir": tmp_path / "private",
    }
    return HostedSettings(**(values | overrides))


def headers(token):
    return {"authorization": f"Bearer {token}"}


@asynccontextmanager
async def app_client(tmp_path, *, blocking_upstream=None, blocking_issuer=None, **overrides):
    introspections = []
    upstream = []
    config = configuration(tmp_path, **overrides)

    async def issuer(request):
        assert request.url == f"{ISSUER}/introspect"
        assert (
            request.headers["authorization"]
            == httpx.BasicAuth("server-client", "fixture-secret")._auth_header
        )
        token = parse_qs(request.content.decode())["token"][0]
        introspections.append(token)
        if blocking_issuer:
            await blocking_issuer()
        if token == "issuer-unavailable":
            return httpx.Response(302, headers={"location": "https://untrusted.example.test"})
        claims = {
            "active": True,
            "iss": ISSUER,
            "exp": int(time.time()) + 60,
            "sub": "alice",
            "client_id": "fixture-client",
            "aud": RESOURCE,
            "scope": f"{READ} {REPORTS}",
        }
        changes = {subject: {"sub": subject} for subject in config.subjects} | {
            "reader": {"sub": "reader", "scope": READ},
            "no-scope": {"scope": "other"},
            "expired": {"exp": 1},
            "wrong-issuer": {"iss": ORIGIN},
            "wrong-aud": {"aud": f"{ORIGIN}/different"},
            "wrong-client": {"client_id": "unregistered"},
            "uninvited": {"sub": "uninvited"},
        }
        body = claims | changes[token] if token in changes else {"active": False}
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(body).encode()))

    async def source(request):
        assert "authorization" not in request.headers
        upstream.append(request.url.path)
        if request.url.path == "/api/tr/dataflows":
            if blocking_upstream:
                started, release = blocking_upstream
                started.set()
                await release.wait()
            return httpx.Response(200, content=(FIXTURES / "flows.json").read_bytes())
        if request.url.path.endswith("/file/csv"):
            return httpx.Response(
                200,
                content=(FIXTURES / "data.csv").read_bytes(),
                headers={"content-type": "text/csv"},
            )
        raise AssertionError("Unexpected upstream call")

    app = create_hosted_app(
        config,
        issuer_transport=httpx.MockTransport(issuer),
        upstream_transport=httpx.MockTransport(source),
    )
    async with app.router.lifespan_context(app):
        yield httpx.ASGITransport(app=app), introspections, upstream


async def test_origin_auth_scope_and_wire_limits(tmp_path):
    async with (
        app_client(tmp_path) as (transport, introspections, upstream),
        httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=ACCEPT) as client,
    ):
        bad_origin = await client.post(
            "/mcp", json=INIT, headers=headers("alice") | {"origin": ISSUER}
        )
        bad_host = await client.post(
            "/mcp", json=INIT, headers=headers("alice") | {"host": "untrusted.example.test"}
        )
        assert bad_origin.status_code == bad_host.status_code == 403
        assert introspections == []

        missing = await client.post("/mcp", json=INIT)
        assert missing.status_code == 401
        assert f"{ORIGIN}{METADATA}" in missing.headers["www-authenticate"]
        assert missing.headers["cache-control"] == "private, no-store"
        assert (await client.get(METADATA)).json() == {
            "resource": RESOURCE,
            "authorization_servers": [ISSUER],
            "scopes_supported": [READ, REPORTS],
            "bearer_methods_supported": ["header"],
        }
        assert (await client.get("/readyz")).json() == {"status": "ok"}

        for token in (
            "expired",
            "wrong-issuer",
            "wrong-aud",
            "wrong-client",
            "uninvited",
            "unknown",
        ):
            assert (await client.post("/mcp", json=INIT, headers=headers(token))).status_code == 401
        assert (
            await client.post("/mcp", json=INIT, headers=headers("issuer-unavailable"))
        ).status_code == 503
        assert (
            await client.post("/mcp", json=INIT, headers=headers("no-scope"))
        ).status_code == 403

        for method, params in (
            ("resources/read", {"uri": "tuik-artifact://art_invalid/a.png"}),
            ("tools/call", {"name": "chart_tuik_data", "arguments": {"save": "yes"}}),
        ):
            denied = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
                headers=headers("reader"),
            )
            assert denied.status_code == 403
            assert f'scope="{READ} {REPORTS}"' in denied.headers["www-authenticate"]
        big = await client.post(
            "/mcp",
            content=b"x" * (MAX_REQUEST + 1),
            headers=headers("alice") | {"content-type": "application/json"},
        )
        assert big.status_code == 413
        response = await client.post("/mcp", json=INIT, headers=headers("alice"))
        assert response.status_code == 200
        assert "mcp-session-id" not in response.headers
        assert upstream == []
        assert (await client.get("/mcp", headers=headers("alice"))).status_code == 405


@asynccontextmanager
async def session(transport, subject):
    async with (
        httpx.AsyncClient(transport=transport, headers=headers(subject)) as http,
        streamable_http_client(RESOURCE, http_client=http) as (read, write, session_id),
        ClientSession(read, write) as client,
    ):
        await client.initialize()
        assert session_id() is None
        yield client


async def test_two_identities_share_source_and_keep_owned_data_across_restart(tmp_path):
    pytest.importorskip("matplotlib")
    args = {
        "resource_id": FLOW,
        "x": ["Zaman (2015-01)"],
        "y": ["Gözlem Değeri"],
        "decimal_separator": ",",
        "save": True,
    }
    async with app_client(tmp_path) as (transport, _, upstream), AsyncExitStack() as stack:
        alice = await stack.enter_async_context(session(transport, "alice"))
        bob = await stack.enter_async_context(session(transport, "bob"))
        tools = {tool.name for tool in (await alice.list_tools()).tools}
        assert "get_tuik_data" in tools
        assert "read_tuik_document" not in tools
        first, second = await asyncio.gather(
            alice.call_tool("get_tuik_data", QUERY),
            bob.call_tool("get_tuik_data", QUERY),
        )
        first, second = first.structuredContent, second.structuredContent
        assert first["rows"] == second["rows"] == [{"Gözlem Değeri": "98,4622068329301"}]
        assert first["next_cursor"] != second["next_cursor"]
        resume = {"resource_id": FLOW, "cursor": first["next_cursor"], "limit": 1}
        assert (await bob.call_tool("get_tuik_data", resume)).structuredContent["code"] == (
            "invalid_cursor"
        )
        assert (await alice.call_tool("get_tuik_data", resume)).structuredContent["rows"] == [
            {"Gözlem Değeri": "101,664854746884"}
        ]
        chart_result = await alice.call_tool("chart_tuik_data", args)
        chart = chart_result.structuredContent
        assert chart and chart["status"] == "ok", chart_result
        assert all(item.type != "image" for item in chart_result.content)
        artifact = chart["artifact"]
        (png,) = [item for item in artifact["files"] if item["mime_type"] == "image/png"]
        (content,) = (await alice.read_resource(png["uri"])).contents
        assert base64.b64decode(content.blob).startswith(b"\x89PNG\r\n")
        with pytest.raises(McpError, match="not_found"):
            await bob.read_resource(png["uri"])
        assert upstream.count("/api/tr/dataflows") == 1
        assert sum(path.endswith("/file/csv") for path in upstream) == 1

    async with (
        app_client(tmp_path) as (transport, _, upstream),
        session(transport, "alice") as alice,
    ):
        assert (await alice.call_tool("get_tuik_data", resume)).structuredContent["code"] == (
            "invalid_cursor"
        )
        (restored,) = (await alice.read_resource(png["uri"])).contents
        assert restored.blob == content.blob
        assert upstream == []


async def test_rate_and_active_call_admission(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    async with (
        app_client(tmp_path, blocking_upstream=(started, release)) as (transport, _, _),
        httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=ACCEPT) as client,
        session(transport, "alice") as alice,
    ):
        first = asyncio.create_task(alice.call_tool("get_tuik_data", QUERY))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            busy = await alice.call_tool("get_tuik_data", QUERY)
            assert busy.isError and "pilot_busy" in busy.content[0].text
        finally:
            release.set()
            assert (await first).structuredContent["rows"]

        without_query = {key: value for key, value in QUERY.items() if key != "query"}
        assert (await alice.call_tool("get_tuik_data", without_query)).structuredContent[
            "next_cursor"
        ]
        parse_limit = await alice.call_tool("get_tuik_data", QUERY)
        assert parse_limit.isError and "parse_limit" in parse_limit.content[0].text

        statuses = [
            (await client.post("/mcp", json=INIT, headers=headers("bob"))).status_code
            for _ in range(11)
        ]
        assert statuses == [200] * 10 + [429]


async def test_failed_credentials_do_not_spend_authenticated_quota(tmp_path, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 26, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr("tuik_mcp.hosted_limits.datetime", FixedDateTime)
    monkeypatch.setattr("tuik_mcp.hosted_limits.monotonic", lambda: 120)
    subjects = {"alice", "bob", "reader", "deniz", "ece", "mert", "zeynep"}
    async with (
        app_client(tmp_path, subjects=subjects) as (transport, introspections, _),
        httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=ACCEPT) as client,
    ):
        for _ in range(29):
            assert (await client.post("/mcp", json=INIT)).status_code == 401
        assert introspections == []
        assert (await client.post("/mcp", json=INIT, headers=headers("unknown"))).status_code == 401
        assert (await client.post("/mcp", json=INIT)).status_code == 429
        assert (await client.post("/mcp", json=INIT, headers=headers("expired"))).status_code == 429

        for subject in sorted(subjects - {"zeynep"}):
            for _ in range(10):
                response = await client.post("/mcp", json=INIT, headers=headers(subject))
                assert response.status_code == 200
        excess = await client.post("/mcp", json=INIT, headers=headers("zeynep"))
        assert excess.status_code == 429
        assert excess.headers["retry-after"] == "60"


async def test_issuer_admission_releases_slots(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    count = 0

    async def block_introspection():
        nonlocal count
        count += 1
        if count == 4:
            started.set()
        await release.wait()

    async with (
        app_client(tmp_path, blocking_issuer=block_introspection) as (transport, introspections, _),
        httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=ACCEPT) as client,
    ):
        async with asyncio.TaskGroup() as group:
            active = [
                group.create_task(client.post("/mcp", json=INIT, headers=headers("unknown")))
                for _ in range(4)
            ]
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                excess = await client.post("/mcp", json=INIT, headers=headers("bob"))
                assert excess.status_code == 503
                assert introspections == ["unknown"] * 4
            finally:
                release.set()
        assert all(request.result().status_code == 401 for request in active)
        assert (await client.post("/mcp", json=INIT, headers=headers("bob"))).status_code == 200


def test_issuer_introspection_cannot_forward_to_other_origin(tmp_path):
    with pytest.raises(ValueError, match="issuer HTTPS origin"):
        configuration(tmp_path, introspection_url="https://other.example.test/introspect")
