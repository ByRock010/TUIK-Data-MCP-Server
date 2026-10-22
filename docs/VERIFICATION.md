# MVP verification

## Excel/PDF development increment — 2026-10-22

Feature branches for #9 → #6 → #7 add bounded file parsing/caching, Excel rows
through `get_tuik_data` and the seventh tool, `read_tuik_document`. This is
pre-release verification; issue #10 still owns v0.2.0 release/tag closure.

- Offline results and design are recorded in the dated issue reports below.
- A real MCP stdio smoke run passed with all seven tools, eight original Turkish
  queries plus publication discovery, Excel rows and CSV retrieval.
- Publication PDF: 17 pages, first-page Turkish text returned.
- Methodology PDF: 10 pages, physical page 3 returned. A 120-character cap produced
  `next_page: 3, next_offset: 120`; continuation returned offset 120 on that same
  page and advanced its offset to 240, retaining the file retrieval timestamp.
- Separate #6 live acceptance passed for TÜFE (`2025=100`), the XLS-only population
  bulletin (`press:53899`, 82 source rows) and an explicit labour workbook.
- `press:54072` still advertises a first download that returned HTTP 404; this is
  documented as an upstream limitation, not hidden by a fallback to another table.

Evidence: [live report](verification/file-reading-live-2026-10-22.json),
[Excel verification](verification/issue-6-2026-10-22.md),
[PDF/limits verification](verification/issue-7-9-2026-10-22.md),
[design and research](FILE_READING_DESIGN.md),
[23 October demo](MEETING_DEMO_2026-10-23.md).


## Dataset discovery increment — 2026-10-17

Added `search_tuik_datasets` with Turkish text matching, optional exact major-theme
filtering, public-export filtering enabled by default, reusable catalog IDs, and
advertised formats/download links. Discovery and detail share a one-hour catalog
cache and preserve the catalog retrieval timestamp.

- `.venv/bin/pytest -q`: **59 passed, 1 deselected**. New tests cover discovery →
  metadata → CSV retrieval, result ranking/counts, Turkish casing, theme and export
  filters, cache sharing under concurrent requests, fresh-session ID resolution,
  empty catalogs, invalid inputs, and malformed upstream records.
- Ruff lint and format checks passed.
- The full live stdio workflow passed with **all six tools**, all eight original
  Turkish portal queries, bulletin detail, XLS download, themes and classifications.
- The live script now discovers **Altın Hariç Dış Ticaret Hadleri** through the new
  tool and uses its returned ID to retrieve metadata and CSV data. It returned
  `flow:DF_ALTINSIZ_DTH+V1.0`, with **2 of 11,564 rows** and **16 columns**.
- A separate direct catalog check found **510 entries**, **342 advertising exports**.
  Export availability is not verified by discovery; file retrieval still verifies it.

Evidence: [live-verification-2026-10-17.json](live-verification-2026-10-17.json).
No new endpoints, databases, licensing changes or external service connections
were introduced. The original MVP run is preserved below.

## Original MVP — 2026-10-13

Verified on **2026-10-13**, Europe/Istanbul, using Python **3.14.0**, official MCP
SDK **1.30.0**, httpx **0.28.1**, and pytest **9.1.1**. The package requires Python
3.12+; this run did not independently test Python 3.12 or 3.13.

## Executed checks

- Editable installation: `uv pip install --python .venv/bin/python -e '.[dev]'` succeeded.
- `.venv/bin/pytest -q`: **44 passed, 1 deselected**. The deselected test is the
  opt-in network workflow, also available with `pytest -m live`.
- `.venv/bin/ruff check src tests scripts`: passed.
- `.venv/bin/ruff format --check src tests scripts`: passed.
- `.venv/bin/python scripts/live_smoke.py`: **passed**, using an actual MCP client
  and a newly launched stdio server process. This is the same workflow invoked by
  the optional live pytest test; it was executed directly to capture a JSON report.
- All five tools were discoverable and returned structured MCP content.
- Dockerfile provided; image build/run **not verified** because the local Docker
  daemon was not running (`Cannot connect to the Docker daemon`).

Offline tests cover Turkish normalization, query/ID validation, successful and
empty search, metadata, classifications/themes, source provenance, caching and
expiry, bounded data rows, exact filters, unsupported data, timeout/retry handling,
HTTP errors, malformed JSON/CSV, compressed responses, response size limits,
unapproved hosts/redirects, and stdio tool discovery/input validation.

## Verified live searches

Every query returned real results. These are examples, not asserted statistical
values, and the source may subsequently publish revisions.

| Query | Example result |
| --- | --- |
| `işsizlik` | İşsizlik Oranı; Genç İşsizlik Oranı; İşgücü İstatistikleri, Ağustos 2026 |
| `nüfus` | Nüfus İstatistikleri Portalı; Adrese Dayalı Nüfus Kayıt Sistemi Sonuçları, 2025 |
| `enflasyon` | Tüketici Fiyat Endeksi, Eylül 2026 |
| `TÜFE` | Tüketici Fiyat Endeksi, Eylül 2026 |
| `dış ticaret` | Dış Ticaret Endeksleri, Ağustos 2026; Dış Ticaret Hadleri |
| `İstanbul nüfusu` | Belediye Sayısı ve Nüfusu; İl ve Cinsiyete Göre İl/İlçe Merkezi, Belde/ Köy Nüfusu |
| `genç işsizlik` | Genç İşsizlik Oranı; Cinsiyete Göre Genç İşsizlik Oranı |
| `konut satışları` | Konut ve İş Yeri Satış İstatistikleri, Ağustos 2026 |

Search relevance is controlled by TÜİK; a city query can return a nationwide table
with city dimensions. The MVP does not claim to apply geographic filters to search
results or to rank by newest publication.

## Detail and retrieval

- `get_tuik_resource("press:57987")` returned **İşgücü İstatistikleri, Ağustos 2026**,
  metadata and nine advertised downloads. Official source:
  <https://veriportali.tuik.gov.tr/tr/press/57987>.
- A table ID returned by `search_tuik("işsizlik")` yielded
  `status: download_available`; the XLS **İşsizlik Oranı** was fetched successfully
  and verified as an OLE spreadsheet, **71,168 bytes**. Its complete official URL
  and opaque ID are in the JSON report; those links can change.
- `get_tuik_data("flow:DF_ALTINSIZ_DTH+V1.0", limit=2)` returned `status: ok`,
  **16 columns**, **2 rows**, `total_rows: 11564`, and `truncated: true` from the
  official CSV export:
  <https://veriportali.tuik.gov.tr/api/tr/dataflows/DF_ALTINSIZ_DTH%2BV1.0/file/csv>.
  Values remain original strings; no statistical interpretation was tested.
- `list_tuik_themes()` returned **19** major themes with official source URLs.
- `search_tuik_classifications("NACE", limit=3)` returned **NACE Rev.1**,
  **NACE Rev.1.1**, and **NACE Rev.2**, with Classification Server edition URLs.

The full machine-readable evidence is [live-verification.json](live-verification.json).
It is a development report, not a runtime catalog or fallback data source.

## Practical boundaries

These checks establish that the server and verified public operations worked from
this machine at the stated time. They do not establish permanent availability of
TÜİK's frontend-internal API, universal CSV export coverage, or authenticated SDMX
access. The adapter inventory and unsuccessful investigations are recorded in
[TUIK_SOURCES.md](TUIK_SOURCES.md).
