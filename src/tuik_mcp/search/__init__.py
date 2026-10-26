"""Packaged analysis design for the optional future OpenSearch backend."""

import json
from importlib.resources import files


def analysis_settings() -> dict:
    """Return fresh index settings with the same versioned synonym rules in both paths.

    No OpenSearch client import or network access occurs here. #14 combines these
    settings with mappings; #17 decides routing and relevance query construction.
    """
    resources = files(__package__)
    settings = json.loads(resources.joinpath("analysis.json").read_text(encoding="utf-8"))
    rules = [
        line.strip()
        for line in resources.joinpath("synonyms.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for name in ("tuik_tr_synonyms", "tuik_ascii_synonyms"):
        settings["analysis"]["filter"][name]["synonyms"] = rules.copy()
    return settings
