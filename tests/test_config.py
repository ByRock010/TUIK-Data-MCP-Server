import pytest
from pydantic import ValidationError

from tuik_mcp.config import Settings


def test_search_is_optional_and_env_settings_are_read(monkeypatch):
    monkeypatch.delenv("TUIK_OPENSEARCH_URL", raising=False)
    monkeypatch.delenv("TUIK_OPENSEARCH_INDEX", raising=False)
    assert Settings.from_env().opensearch_url is None
    assert Settings.from_env().opensearch_index == "tuik-resources"
    monkeypatch.setenv("TUIK_OPENSEARCH_URL", "http://localhost:9200")
    monkeypatch.setenv("TUIK_OPENSEARCH_INDEX", "tuik-test-resources")
    settings = Settings.from_env()
    assert str(settings.opensearch_url) == "http://localhost:9200/"
    assert settings.opensearch_index == "tuik-test-resources"


@pytest.mark.parametrize("url", ["", " ", "\t\n"])
def test_blank_search_url_disables_optional_backend(monkeypatch, url):
    monkeypatch.setenv("TUIK_OPENSEARCH_URL", url)
    assert Settings.from_env().opensearch_url is None
    assert Settings(opensearch_url=url).opensearch_url is None


@pytest.mark.parametrize("url", ["localhost:9200", "file:///tmp/index", "ftp://localhost"])
def test_invalid_search_url(url):
    with pytest.raises(ValidationError):
        Settings(opensearch_url=url)


@pytest.mark.parametrize("alias", ["", "Uppercase", "bad alias", "_hidden", "../x", "x" * 256])
def test_invalid_search_alias(alias):
    with pytest.raises(ValidationError):
        Settings(opensearch_index=alias)
