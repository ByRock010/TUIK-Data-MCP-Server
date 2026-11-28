"""Authorization for the local hosted MCP pilot."""

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

READ = "tuik:read"
REPORTS = "tuik:reports"
BEARER = re.compile(r"Bearer ([A-Za-z0-9._~+/=-]{1,4096})", flags=re.IGNORECASE)
CLAIM_BYTES = 16 * 1024


def https_endpoint(value: str):
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or not re.fullmatch(r"[A-Za-z0-9.:-]+", parts.netloc)
        or parts.port == 0
        or not re.fullmatch(r"[/A-Za-z0-9._~-]*", parts.path)
    ):
        raise ValueError("Expected an HTTPS endpoint without credentials, query or fragment")
    return parts


class HostedSettings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid")

    resource_url: str = Field(max_length=512)
    issuer_url: str = Field(max_length=512)
    introspection_url: str = Field(max_length=512)
    introspection_client_id: str = Field(min_length=1, max_length=200)
    introspection_secret: SecretStr = Field(min_length=1, max_length=4096)
    subjects: frozenset[str] = Field(min_length=1, max_length=20)
    client_ids: frozenset[str] = Field(min_length=1, max_length=20)
    artifact_dir: Path

    @model_validator(mode="after")
    def checked_configuration(self):
        resource = https_endpoint(self.resource_url)
        issuer = https_endpoint(self.issuer_url)
        introspection = https_endpoint(self.introspection_url)
        if resource.path != "/mcp":
            raise ValueError("Hosted MCP uses the canonical /mcp resource")
        if issuer.netloc != introspection.netloc or not introspection.path.strip("/"):
            raise ValueError("Introspection must be configured on the issuer HTTPS origin")
        if not self.artifact_dir.is_absolute():
            raise ValueError("Hosted artifacts require a dedicated absolute directory")
        for values in (self.subjects, self.client_ids):
            if any(not value or len(value) > 200 or not value.isprintable() for value in values):
                raise ValueError("Roster entries and registered client IDs must be printable")
        return self

    @property
    def authority(self) -> str:
        return urlsplit(self.resource_url).netloc

    @property
    def origin(self) -> str:
        return "https://" + self.authority

    @classmethod
    def from_env(cls):
        values = {
            name: os.environ[f"TUIK_HOSTED_{name.upper()}"]
            for name in cls.model_fields
            if f"TUIK_HOSTED_{name.upper()}" in os.environ
        }
        for name in ("subjects", "client_ids"):
            if name in values:
                values[name] = json.loads(values[name])
        secret_path = os.environ.get("TUIK_HOSTED_INTROSPECTION_SECRET_FILE")
        if secret_path:
            if "introspection_secret" in values:
                raise ValueError("Configure one introspection secret source")
            with Path(secret_path).open("r", encoding="utf-8") as secret_file:
                values["introspection_secret"] = secret_file.read(4097).rstrip("\r\n")
        return cls(**values)


class IssuerUnavailable(Exception):
    """The configured issuer could not safely confirm token state."""


class Identity(BaseModel):
    subject: str
    scopes: frozenset[str]


class IntrospectionVerifier:
    def __init__(self, settings: HostedSettings, http: httpx.AsyncClient):
        self.settings = settings
        self.http = http

    async def verify(self, token: str) -> Identity | None:
        # Credentials and the caller's token go exclusively to the configured issuer.
        try:
            async with self.http.stream(
                "POST",
                self.settings.introspection_url,
                data={"token": token, "token_type_hint": "access_token"},
                auth=(
                    self.settings.introspection_client_id,
                    self.settings.introspection_secret.get_secret_value(),
                ),
                headers={"accept": "application/json", "accept-encoding": "identity"},
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise IssuerUnavailable
                body = bytearray()
                async for chunk in response.aiter_raw():
                    if len(body) + len(chunk) > CLAIM_BYTES:
                        raise IssuerUnavailable
                    body.extend(chunk)
            claims = json.loads(body)
        except (httpx.HTTPError, ValueError, RecursionError) as exc:
            raise IssuerUnavailable from exc
        if not isinstance(claims, dict):
            raise IssuerUnavailable
        if claims.get("active") is not True:
            return None
        issuer, exp = claims.get("iss"), claims.get("exp")
        subject, client, audience = claims.get("sub"), claims.get("client_id"), claims.get("aud")
        scopes = claims.get("scope", "")
        audiences = [audience] if isinstance(audience, str) else audience
        not_before = claims.get("nbf", 0)
        current = int(time.time())
        if (
            issuer != self.settings.issuer_url
            or type(exp) is not int
            or exp <= current
            or type(not_before) is not int
            or not_before > current
            or not isinstance(audiences, list)
            or not all(isinstance(item, str) for item in audiences)
            or self.settings.resource_url not in audiences
            or not isinstance(subject, str)
            or subject not in self.settings.subjects
            or not isinstance(client, str)
            or client not in self.settings.client_ids
            or not isinstance(scopes, str)
        ):
            return None
        return Identity(subject=subject, scopes=frozenset(scopes.split()))
