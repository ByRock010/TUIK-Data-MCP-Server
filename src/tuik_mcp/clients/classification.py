from urllib.parse import urlencode

from pydantic import BaseModel

from tuik_mcp.config import CLASSIFICATION
from tuik_mcp.models.search import TuikSearchResult

from .base import HttpClient, SourceError
from .tuik_portal import parse


class ClassificationRecord(BaseModel):
    Id: int
    Ad: str
    Kisa_Ad: str


class ClassificationClient:
    def __init__(self, http: HttpClient):
        self.http = http

    async def search(self, query: str) -> list[TuikSearchResult]:
        url = f"{CLASSIFICATION}/Classifications/GetSiniflamalarSahibi?" + urlencode(
            {"tur": 0, "searchname": query}
        )
        payload = await self.http.json("GET", url)
        if not isinstance(payload, list):
            raise SourceError("malformed_response", "Expected classification records", url)
        records = [parse(ClassificationRecord, item, url) for item in payload]
        return [
            TuikSearchResult(
                id=f"classification:{item.Id}",
                title=item.Kisa_Ad,
                description=item.Ad,
                resource_type="classification",
                source_url=f"{CLASSIFICATION}/Classifications/ClassificationsSatir?surumId={item.Id}",
            )
            for item in records
        ]
