"""Compare the public DataBrowser and portal CSV snapshots for issue #104."""

import argparse
import csv
import hashlib
import io
import json
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

CONFIG = Path(__file__).with_name("samples.json")
CASE = str.maketrans({"İ": "i", "I": "ı"})
ATTRIBUTE_HEADERS = {
    "UNIT_MEASURE": "Ölçü Birimi",
    "UNIT_MULT": "Birim Çarpanı",
    "OBS_STATUS": "Gözlem Durumu",
    "CONF_STATUS": "Gizlilik Durumu",
    "TIME_FORMAT": "Zaman Formatı",
    "DECIMAL": "Ondalık Basamak Sayısı",
    "DECIMALS": "Ondalık Basamak Sayısı",
    "TRANSFORMATION": "Dönüşüm",
}


def label(value):
    normalized = unicodedata.normalize("NFC", value).translate(CASE).casefold()
    return " ".join(normalized.split())


def csv_rows(body, delimiter):
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")), delimiter=delimiter)
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("Missing or duplicate CSV headers")
    rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise ValueError("Ragged CSV")
    return reader.fieldnames, rows


def identity(item):
    return f"{item['agencyID']}:{item['id']}({item['version']})"


def urn(value):
    return (value or "").split("=", 1)[-1]


def source_labels(item):
    return item.get("names") or {}


def components(document, flow_id):
    data = document["data"]
    flows = [flow for flow in data["dataflows"] if identity(flow) == flow_id]
    if len(flows) != 1:
        raise ValueError(f"Expected one exact version for {flow_id}")
    dsd_id = urn(flows[0]["structure"])
    (dsd,) = [dsd for dsd in data["dataStructures"] if identity(dsd) == dsd_id]
    schemes = {
        f"{identity(scheme)}.{concept['id']}": source_labels(concept)
        for scheme in data["conceptSchemes"]
        for concept in scheme["concepts"]
    }
    codelists = {identity(codelist): codelist["codes"] for codelist in data["codelists"]}
    dsd_components = dsd["dataStructureComponents"]
    dimensions = dsd_components["dimensionList"]
    ordered = sorted(dimensions["dimensions"], key=lambda item: item["position"])
    attrs = dsd_components["attributeList"]["attributes"]
    result = {}
    for item in [*ordered, *dimensions["timeDimensions"], *attrs]:
        codelist = urn((item.get("localRepresentation") or {}).get("enumeration"))
        names = schemes.get(urn(item.get("conceptIdentity")), {})
        result[item["id"]] = {
            "label": names.get("tr"),
            "codelist": codelist or None,
            "values": {code["id"]: source_labels(code) for code in codelists.get(codelist, [])},
        }
    return dsd_id, result, [item["id"] for item in attrs]


def matching_header(headers, name):
    if not name:
        return None
    matches = [
        column
        for column in headers
        if label(column) == label(name) or label(column).startswith(label(name) + " (")
    ]
    return matches[0] if len(matches) == 1 else None


def translated(component, code):
    return component["values"].get(code, {}).get("tr", code)


def numeric_equal(coded, display):
    # SDMX-CSV uses a dot. The Turkish display export uses a decimal comma.
    if not coded or not display:
        return coded == display
    try:
        return Decimal(coded) == Decimal(display.replace(",", "."))
    except InvalidOperation:
        return coded == display


def summarize(root, name, sample, catalog):
    code, version = sample["id"].split("+V")
    flow_id = f"TR:{code}({version})"
    required = [
        "view.json",
        "dsd.json",
        "portal.csv",
        "broad-count.json",
        "count.json",
        "selected.csv",
    ]
    files = {suffix: root / f"{name}-{suffix}" for suffix in required}
    bodies = {suffix: path.read_bytes() for suffix, path in files.items() if path.exists()}
    result = {
        "id": f"flow:{sample['id']}",
        "catalog_updated_at": catalog.get("updatedAt"),
        "catalog_period": catalog.get("period"),
        "inputs": {
            suffix: {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            for suffix, body in bodies.items()
        },
        "missing_inputs": sorted(set(required) - bodies.keys()),
    }
    if result["missing_inputs"]:
        return result
    view = json.loads(bodies["view.json"])
    dsd_id, metadata, attributes = components(json.loads(bodies["dsd.json"]), flow_id)
    browser_headers, browser_rows = csv_rows(bodies["selected.csv"], ",")
    portal_headers, portal_rows = csv_rows(bodies["portal.csv"], ";")
    dimension_order = [
        column
        for column in browser_headers
        if column
        not in {
            "DATAFLOW",
            "OBS_VALUE",
            *attributes,
        }
    ]
    assert dimension_order == [column for column in metadata if column not in attributes]
    assert all(row["DATAFLOW"] == flow_id for row in browser_rows)
    assert all(row["TIME_PERIOD"] == sample["period"] for row in browser_rows)
    for dimension, codes in sample["codes"].items():
        assert set(codes) <= metadata[dimension]["values"].keys()
        assert all(row[dimension] in codes for row in browser_rows)
    selected_count = json.loads(bodies["count.json"])["obsCount"]
    broad_count = json.loads(bodies["broad-count.json"])["obsCount"]
    assert 0 < selected_count <= 200 and len(browser_rows) == selected_count
    assert selected_count < broad_count

    view_labels = {item["id"]: item["label"] for item in view["criteria"]}
    columns = {}
    for dimension in dimension_order:
        source_name = sample.get("headers", {}).get(dimension)
        candidates = (source_name, view_labels.get(dimension), metadata[dimension]["label"])
        for candidate in candidates:
            if header := matching_header(portal_headers, candidate):
                columns[dimension] = header
                break
    assert "TIME_PERIOD" in columns and set(sample["codes"]) <= columns.keys(), columns
    assert len(set(columns.values())) == len(columns)
    numeric_header = matching_header(portal_headers, sample.get("value_header", "Gözlem Değeri"))
    assert numeric_header
    attribute_columns = {
        attr: matching_header(portal_headers, ATTRIBUTE_HEADERS.get(attr)) for attr in attributes
    }
    filter_labels = {
        columns[dimension]: {label(translated(metadata[dimension], code)) for code in codes}
        for dimension, codes in sample["codes"].items()
    }
    groups = defaultdict(list)
    for row in portal_rows:
        if row[columns["TIME_PERIOD"]] != sample["period"]:
            continue
        if all(label(row[column]) in candidates for column, candidates in filter_labels.items()):
            groups[tuple(label(row[column]) for column in columns.values())].append(row)
    keys = [
        tuple(label(translated(metadata[dimension], row[dimension])) for dimension in columns)
        for row in browser_rows
    ]
    duplicates = {key for key, count in Counter(keys).items() if count > 1}
    matched = []
    discrepancies = []
    for key, row in zip(keys, browser_rows, strict=True):
        source = groups[key]
        record = {dimension: row[dimension] for dimension in dimension_order}
        record["dataflow_value"] = row["OBS_VALUE"]
        record["attributes"] = {attr: row[attr] for attr in attributes}
        if key in duplicates or len(source) != 1:
            discrepancies.append(
                {
                    "reason": "ambiguous" if source else "missing_portal_key",
                    "portal_matches": len(source),
                    "browser": record,
                }
            )
            continue
        portal_row = source[0]
        record["portal_value"] = portal_row[numeric_header]
        record["equal_decimal"] = numeric_equal(record["dataflow_value"], record["portal_value"])
        status_differences = {}
        for attr, column in attribute_columns.items():
            if column and portal_row[column] not in {
                row[attr],
                translated(metadata[attr], row[attr]),
            }:
                status_differences[attr] = [row[attr], portal_row[column]]
        if not record["equal_decimal"] or status_differences:
            discrepancies.append(
                {
                    "reason": "value_or_attribute",
                    "browser": record,
                    "attributes": status_differences,
                }
            )
        matched.append(record)
    for key in sorted(groups.keys() - set(keys)):
        portal_only = groups[key]
        discrepancies.append(
            {
                "reason": "missing_browser_key",
                "portal_labels": dict(zip(columns, key, strict=True)),
                "portal_values": [row[numeric_header] for row in portal_only],
            }
        )

    result.update(
        {
            "dsd_id": dsd_id,
            "dsd_order": dimension_order,
            "mapped_headers": columns,
            "attribute_headers": attribute_columns,
            "observed_codes": {
                dim: {
                    "codelist": metadata[dim]["codelist"],
                    "labels": {
                        value: metadata[dim]["values"].get(value, {})
                        for value in sorted({row[dim] for row in browser_rows})
                    },
                }
                for dim in dimension_order
                if dim != "TIME_PERIOD"
            },
            "observed_attribute_codes": {
                attr: {
                    "codelist": metadata[attr]["codelist"],
                    "labels": {
                        value: metadata[attr]["values"].get(value, {})
                        for value in sorted({row[attr] for row in browser_rows if row[attr]})
                    },
                }
                for attr in attributes
            },
            "broad_period_count": broad_count,
            "selection_count": selected_count,
            "portal_rows": len(portal_rows),
            "portal_selection_rows": sum(map(len, groups.values())),
            "matched_rows": matched,
            "discrepancies": discrepancies,
        }
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_directory", type=Path)
    args = parser.parse_args()
    root = args.input_directory
    catalog_body = (root / "catalog.json").read_bytes()
    catalog = json.loads(catalog_body)
    by_id = {row["id"]: row for row in catalog}
    assert len(by_id) == len(catalog)
    config = json.loads(CONFIG.read_bytes())
    report = {
        "catalog": {
            "rows": len(catalog),
            "advertised_downloadable": sum(row["downloadable"] is True for row in catalog),
            "bytes": len(catalog_body),
            "sha256": hashlib.sha256(catalog_body).hexdigest(),
        },
        "domains": {
            name: summarize(root, name, sample, by_id[sample["id"]])
            for name, sample in config.items()
        },
    }
    historic = (root / "prices-historic-selected.csv").read_bytes()
    _, rows = csv_rows(historic, ",")
    price_portal = (root / "prices-portal.csv").read_bytes()
    headers, portal_rows = csv_rows(price_portal, ";")
    period_header = matching_header(headers, "Zaman")
    report["prices_historic_probe"] = {
        "period": "2024-01",
        "portal_periods": sorted({row[period_header] for row in portal_rows}),
        "browser_count": json.loads((root / "prices-historic-count.json").read_bytes())["obsCount"],
        "browser_rows": rows,
        "browser_csv_bytes": len(historic),
        "browser_csv_sha256": hashlib.sha256(historic).hexdigest(),
    }
    geography_probe = (root / "prices-empty-geography-count.json").read_bytes()
    report["prices_empty_geography_probe"] = {
        "body": json.loads(geography_probe),
        "bytes": len(geography_probe),
        "sha256": hashlib.sha256(geography_probe).hexdigest(),
    }
    report["anonymous_sdmx"] = json.loads((root / "anonymous-sdmx.json").read_bytes())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
