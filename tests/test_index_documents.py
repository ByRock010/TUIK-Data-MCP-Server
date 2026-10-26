from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from tuik_mcp.search.documents import IndexDocument
from tuik_mcp.search.schema import index_definition, physical_index_name


def document(**updates):
    values = {
        "id": "press:57987",
        "kind": "bulletin",
        "title": "İşgücü İstatistikleri",
        "source_url": "https://veriportali.tuik.gov.tr/tr/press/57987",
    }
    return IndexDocument(**(values | updates))


def test_stable_identity_and_metadata_parent():
    assert document().id == "press:57987"
    assert document(id="flow:DF_TEST+V1.0", kind="dataset").kind == "dataset"
    assert document(id="press:57987:metadata", kind="metadata", parent_id="press:57987").parent_id
    for changes in [
        {"id": "resource:" + "a" * 64},
        {"kind": "dataset"},
        {"kind": "table"},
        {"id": "classification:191"},
        {"id": "press:57987:file:table~0123456789"},
        {"id": "press:57987:metadata", "kind": "metadata"},
        {"id": "press:57987:metadata", "kind": "metadata", "parent_id": "press:1"},
        {"parent_id": "press:1"},
        {"related_ids": ["press:57987"]},
        {"related_ids": ["resource:" + "a" * 64]},
    ]:
        with pytest.raises(ValidationError):
            document(**changes)


def test_explicit_dates_source_provenance_and_keywords():
    value = document(
        publication_date="2026-10-12",
        updated_at="2026-10-12T10:00:00+03:00",
        theme=["İstihdam", "İstihdam"],
        formats=["xls", "xls"],
        related_ids=["press:1", "press:1"],
    )
    payload = value.model_dump(mode="json")
    assert payload["publication_date"] == "2026-10-12"
    assert payload["updated_at"] == "2026-10-12T10:00:00+03:00"
    assert value.publication_date == date(2026, 10, 12)
    assert value.theme == ["İstihdam"] and value.formats == ["xls"]
    assert value.related_ids == ["press:1"]
    assert value.indexed_at.tzinfo is not None
    for changes in [
        {"publication_date": "12/10/2026"},
        {"updated_at": "2026-10-12T10:00:00"},
        {"publication_date": 2026},
        {"updated_at": datetime(2026, 1, 1)},
        {"source_url": "https://example.com/"},
        {"title": " "},
        {"downloadable": "true"},
        {"theme": [""]},
        {"formats": ["XLS"]},
        {"unknown_field": 1},
    ]:
        with pytest.raises(ValidationError):
            document(**changes)
    assert document(updated_at=datetime(2026, 1, 1, tzinfo=UTC)).updated_at.tzinfo is not None


def test_model_mapping_covers_every_field_and_keeps_filters_unanalyzed():
    definition = index_definition()
    mapping = definition["mappings"]
    assert mapping["dynamic"] == "strict"
    assert set(mapping["properties"]) == set(IndexDocument.model_fields)
    assert mapping["properties"]["theme"]["type"] == "keyword"
    fields = mapping["properties"]["title"]["fields"]
    assert set(fields) == {"ascii", "keyword", "suggest"}
    assert fields["ascii"]["analyzer"] == "tuik_ascii_index"
    definition["mappings"]["properties"].clear()
    assert index_definition()["mappings"]["properties"]


def test_versioned_index_names():
    assert physical_index_name("tuik-resources", 3) == "tuik-resources-v3"
    for alias, version in [
        ("UPPER", 1),
        ("_bad", 1),
        ("a/b", 1),
        ("a", 0),
        ("a", True),
        ("a" * 255, 1),
    ]:
        with pytest.raises(ValueError):
            physical_index_name(alias, version)
