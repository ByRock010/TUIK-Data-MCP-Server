"""Read the versioned DSD used by the audited public DataBrowser route."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Component:
    id: str
    labels: dict[str, str]
    codelist: str | None
    codes: dict[str, dict[str, str]]

    def describe(self) -> dict:
        return {
            "id": self.id,
            "labels": self.labels,
            "codelist": self.codelist,
            "code_count": len(self.codes),
        }

    def describe_code(self, code: str) -> dict:
        return {"code": code, "labels": self.codes[code]}


@dataclass(frozen=True)
class Structure:
    flow: str
    id: str
    sha256: str
    dimensions: dict[str, Component]
    attributes: dict[str, Component]


def identity(item: dict) -> str:
    return f"{item['agencyID']}:{item['id']}({item['version']})"


def reference(urn: str | None) -> str | None:
    return urn.split("=", 1)[-1] if urn else None


def unique(items, key):
    result = {}
    for item in items:
        name = key(item)
        if name in result:
            raise ValueError("Duplicate DSD identifier")
        result[name] = item
    return result


def labels(item: dict) -> dict[str, str]:
    localized = item.get("names") or {}
    if not isinstance(localized, dict):
        raise ValueError("Malformed multilingual labels")
    return {
        language: value
        for language in ("tr", "en")
        if isinstance((value := localized.get(language)), str)
    }


def read_structure(document: dict, flow: str, signature: str, profile: dict) -> Structure:
    data = document["data"]
    flows = unique(data["dataflows"], identity)
    reference_id = reference(flows[flow]["structure"])
    if reference_id != profile["dsd"]:
        raise ValueError("Unexpected data structure version")
    dsd = unique(data["dataStructures"], identity)[reference_id]["dataStructureComponents"]
    codelists = unique(data["codelists"], identity)
    concepts = {
        f"{identity(scheme)}.{concept['id']}": labels(concept)
        for scheme in data["conceptSchemes"]
        for concept in scheme["concepts"]
    }

    def component(item: dict) -> Component:
        codelist = reference((item.get("localRepresentation") or {}).get("enumeration"))
        codes = unique(codelists[codelist]["codes"], lambda value: value["id"]) if codelist else {}
        return Component(
            id=item["id"],
            labels=concepts.get(reference(item.get("conceptIdentity")), {}),
            codelist=codelist,
            codes={code: labels(value) for code, value in codes.items()},
        )

    listed = dsd["dimensionList"]
    ordered = sorted(
        [*listed["dimensions"], *listed["timeDimensions"]], key=lambda item: item["position"]
    )
    dimensions = {key: component(item) for key, item in unique(ordered, lambda i: i["id"]).items()}
    attributes = {
        key: component(item)
        for key, item in unique(dsd["attributeList"]["attributes"], lambda i: i["id"]).items()
    }
    if list(dimensions) != profile["dimensions"] or set(attributes) != set(profile["attributes"]):
        raise ValueError("DSD dimensions or attribute definitions changed")
    if not dimensions["FREQ"].codes or any(
        not entry.codes for name, entry in dimensions.items() if name != "TIME_PERIOD"
    ):
        raise ValueError("Expected coded dimensions are incomplete")
    return Structure(flow, reference_id, signature, dimensions, attributes)
