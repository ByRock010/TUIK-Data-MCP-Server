"""Index definition without an OpenSearch connection or alias mutation."""

import json
import re
from importlib.resources import files

from . import analysis_settings

SCHEMA_VERSION = 1


def physical_index_name(alias: str, version: int) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", alias):
        raise ValueError("Invalid index alias")
    if type(version) is not int or version < 1:
        raise ValueError("Index version must be a positive integer")
    name = f"{alias}-v{version}"
    if len(name.encode()) > 255:
        raise ValueError("Physical index name exceeds OpenSearch's 255-byte limit")
    return name


def index_definition() -> dict:
    settings = analysis_settings()
    settings.update(number_of_shards=1, number_of_replicas=0)
    # Prefix matching is isolated from full-text relevance and graph synonyms.
    settings["analysis"]["filter"]["tuik_prefix"] = {
        "type": "edge_ngram",
        "min_gram": 2,
        "max_gram": 20,
        "preserve_original": True,
    }
    settings["analysis"]["analyzer"]["tuik_suggest_index"] = {
        "type": "custom",
        "char_filter": ["tuik_apostrophes"],
        "tokenizer": "standard",
        "filter": ["apostrophe", "tuik_lowercase", "asciifolding", "tuik_prefix"],
    }
    mapping = json.loads(files(__package__).joinpath("mapping.json").read_text(encoding="utf-8"))
    return {"settings": settings, "mappings": mapping}
