import csv
import io
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, Field, ValidationError

from tuik_mcp.config import PORTAL
from tuik_mcp.models.dataset import DownloadLink, MetadataSection, TuikResource
from tuik_mcp.models.search import Theme, TuikSearchResult
from tuik_mcp.utils.normalization import official_url, plain_text, resource_id

from .base import HttpClient, SourceError


class SearchItem(BaseModel):
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    type: int
    icon: str | None = None
    content: str | None = None
    date: str | None = None


class SearchPage(BaseModel):
    data: list[SearchItem]
    total: int = Field(ge=0)


class Flow(BaseModel):
    id: str
    name: str
    description: str | None = None
    updatedAt: str | None = None
    downloadable: bool = False
    category: dict = Field(default_factory=dict)
    footnotes: list[str] = Field(default_factory=list)


class Press(BaseModel):
    id: int
    title: str
    period: str | None = None
    date: str | None = None
    content: str | None = None
    tables: list[dict] = Field(default_factory=list)
    statisticalTables: list[dict] = Field(default_factory=list)
    reports: list[dict] = Field(default_factory=list)
    metadatas: list[dict] = Field(default_factory=list)


def parse(model, payload, url):
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise SourceError("malformed_response", "TÜİK JSON has an unexpected schema", url) from exc


def envelope(payload, url):
    if (
        not isinstance(payload, dict)
        or payload.get("isError") is not False
        or "data" not in payload
    ):
        raise SourceError(
            "malformed_response", "TÜİK returned an error or unexpected JSON envelope", url
        )
    return payload["data"]


def normalize_result(item: SearchItem) -> TuikSearchResult:
    url = official_url(item.url)
    kind = {
        1: "bulletin",
        2: "table",
        3: "metadata",
        4: "publication",
        5: "dataset",
        6: "publication",
    }.get(item.type, "unknown")
    if item.icon == "dataflow":
        kind = "dataset"
    return TuikSearchResult(
        id=resource_id(url),
        title=plain_text(item.title) or item.title,
        description=plain_text(item.content),
        resource_type=kind,
        publication_date=item.date,
        source_url=url,
    )


class TuikPortal:
    def __init__(self, http: HttpClient):
        self.http = http

    async def search_page(self, query: str, page: int) -> SearchPage:
        url = f"{PORTAL}/api/tr/data/search"
        payload = await self.http.json(
            "POST",
            url,
            json={
                "text": query,
                "page": page,
                "typeIds": [],
                "categoryIds": [],
                "subCategoryIds": [],
                "years": [],
                "levels": [],
                "archive": False,
                "autoFilter": False,
            },
        )
        return parse(SearchPage, envelope(payload, url), url)

    async def themes(self) -> list[Theme]:
        url = f"{PORTAL}/api/tr/data/statistical-themes"
        items = envelope(await self.http.json("GET", url), url)
        if not isinstance(items, list):
            raise SourceError("malformed_response", "Expected a theme list", url)
        try:
            return [
                Theme(
                    id=item["id"], name=item["name"], source_url=f"{PORTAL}/tr/statistical-themes"
                )
                for item in items
            ]
        except (KeyError, TypeError, ValidationError) as exc:
            raise SourceError("malformed_response", "Invalid TÜİK theme", url) from exc

    async def flows(self) -> list[Flow]:
        url = f"{PORTAL}/api/tr/dataflows"
        payload = await self.http.json("GET", url)
        if not isinstance(payload, list):
            raise SourceError("malformed_response", "Expected a dataflow list", url)
        return [parse(Flow, item, url) for item in payload]

    @staticmethod
    def flow_resource(flow: Flow) -> TuikResource:
        code, version = flow.id.rsplit("+V", 1)
        links = (
            [
                DownloadLink(
                    title=flow.name,
                    format=fmt,
                    source_url=f"{PORTAL}/api/tr/dataflows/{quote(flow.id, safe='')}/file/{fmt}",
                )
                for fmt in ("csv", "json", "xml")
            ]
            if flow.downloadable
            else []
        )
        return TuikResource(
            id="flow:" + flow.id,
            title=flow.name,
            description=plain_text(flow.description),
            resource_type="dataset",
            theme=flow.category.get("name"),
            updated_at=flow.updatedAt,
            source_url=f"https://databrowser2.tuik.gov.tr/#/tr/tuik/TR,{code},{version}",
            download_links=links,
            available_formats=[link.format for link in links],
            notes="\n".join(flow.footnotes) or None,
        )

    async def press(self, resource_id: str) -> TuikResource:
        number = resource_id.split(":")[1]
        url = f"{PORTAL}/api/tr/press/{number}"
        payload = envelope(await self.http.json("GET", url), url)
        if payload is None:
            raise SourceError("not_found", "Bulletin not found", url)
        press = parse(Press, payload, url)
        if str(press.id) != number:
            raise SourceError("malformed_response", "Bulletin identifier mismatch", url)
        links = []
        for entry in press.tables + press.statisticalTables + press.reports:
            try:
                link = official_url(entry["url"])
                if self.is_download(link):
                    links.append(
                        DownloadLink(
                            title=plain_text(entry.get("title")) or press.title,
                            source_url=link,
                            format=entry.get("type"),
                        )
                    )
            except (ValueError, KeyError):
                continue
        sections = []
        try:
            for metadata in press.metadatas:
                for group in metadata.get("data", []):
                    for item in group.get("detail", []):
                        content = plain_text(item.get("content"))
                        if content:
                            sections.append(MetadataSection(title=item["title"], content=content))
        except (TypeError, KeyError, AttributeError) as exc:
            raise SourceError("malformed_response", "Invalid bulletin metadata", url) from exc
        is_metadata = resource_id.endswith(":metadata")
        return TuikResource(
            id=resource_id,
            title=press.title + (f", {press.period}" if press.period else ""),
            description=plain_text(press.content),
            publication_date=press.date,
            resource_type="metadata" if is_metadata else "bulletin",
            source_url=f"{PORTAL}/tr/press/{number}" + ("/metadata" if is_metadata else ""),
            available_formats=sorted({link.format for link in links if link.format}),
            download_links=links,
            metadata=sections,
        )

    @staticmethod
    def is_download(url: str) -> bool:
        parsed = urlsplit(url)
        return (
            parsed.hostname == "veriportali.tuik.gov.tr" and parsed.path == "/api/tr/data/downloads"
        )

    async def download(self, link: DownloadLink) -> tuple[bytes, DownloadLink]:
        response = await self.http.request("GET", link.source_url)
        body = response.content
        mime = response.headers.get("content-type", "").split(";")[0]
        fmt = link.format
        if body.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            fmt = "xls"
        elif body.startswith(b"PK\x03\x04"):
            # ZIP payloads are offered as downloads, never decompressed.
            fmt = "xlsx" if "spreadsheetml" in mime else "zip"
        elif body.startswith(b"%PDF-"):
            fmt = "pdf"
        elif mime in {"text/csv", "application/csv"} and body:
            fmt = "csv"
        else:
            raise SourceError(
                "unsupported_download",
                "The source did not return a recognized table/document file",
                link.source_url,
            )
        return body, link.model_copy(
            update={"format": fmt, "verified": True, "size_bytes": len(body)}
        )

    @staticmethod
    def csv_rows(body: bytes, source_url: str, limit: int, filters: dict[str, str]):
        try:
            text = body.decode("utf-8-sig")
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=";,\t")
            reader = csv.DictReader(io.StringIO(text), dialect=dialect, strict=True)
            columns = reader.fieldnames
            if not columns or len(columns) != len(set(columns)) or any(not c for c in columns):
                raise ValueError("Missing or duplicate CSV columns")
            if set(filters) - set(columns):
                raise SourceError(
                    "invalid_filters",
                    "Use exact column names returned by get_tuik_data",
                    source_url,
                )
            rows, total = [], 0
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError("CSV row width mismatch")
                if all(row[key] == value for key, value in filters.items()):
                    total += 1
                    if len(rows) < limit:
                        rows.append(row)
            return columns, rows, total
        except (UnicodeError, csv.Error, ValueError) as exc:
            raise SourceError(
                "malformed_response", "TÜİK returned unsupported or malformed CSV", source_url
            ) from exc
