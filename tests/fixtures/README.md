Fixtures are reduced snapshots of public TÜİK responses fetched on 2026-10-13.
Search items, catalog records and CSV rows retain upstream values. Search filter
aggregations and most bulletin body/metadata are removed to keep tests small.
The synthetic XLS signature used in conftest.py exists only in tests; live tests
verify the actual official download. Production code never reads these files.

`download_throttle.html` is a reduced copy of the HTML page that
`/api/tr/data/downloads` returned to back-to-back downloads on 2026-10-17.

## Real spreadsheet and PDF corpus (#4, 2026-10-18)

`files/manifest.json` describes 12 unmodified official files (2,638,857 bytes):
9 XLS, 1 real XLSX, the 10-page labour-force methodology PDF, and the 17-page
ICT survey microdata **publication PDF** (the explanatory book, not respondent
records). The seven statistical themes are CPI, labour, foreign trade, ADNKS
population, quarterly GDP, housing sales and industrial production.

Source: **Türkiye İstatistik Kurumu (TÜİK)**. The [official legal notice](https://www.tuik.gov.tr/Kurumsal/Yasal_Uyari)
allows reuse of published data with attribution; rights in the original files
remain with TÜİK. Retain the publisher, source URL, source bulletin/search query,
retrieval time and SHA-256 in the manifest. These fixed snapshots support parser
regression tests; runtime results still come from the official live sources.

### Golden observations

Each file has a `.golden.json` with a hash, title, original bilingual headers,
unit where advertised, first row samples, numeric values/number formats,
notes and source line. Coordinates are zero-based for sheets and one-based for
PDF pages. Sheet ranges use `[row_start, row_end, col_start, col_end]`, with ends
exclusive. Header labels join the observed header rows in source order; duplicate
labels retain column locators and blank columns have explicit placeholders.
`first_rows` retains this source-column structure and uses strings rendered with
cell precision, without thousands separators. These are source observations,
not a claim that #5/#7 ingestion or semantic layout normalization is implemented.
Their tests independently check the source cells, formats, dimensions, merged
ranges, page counts and text anchors. Future parser tests can use those anchors
when checking split bilingual labels, filled year groups and flattened headers.

The corpus exercises two-row and bilingual-in-cell headers, sections, year
forward-fill, merged cells, `General` years, count and decimal formats, missing
markers, footnotes, multi-row headers, very long empty tails and duplicated PDF
glyphs. Notable anchors: CPI has 7 merged ranges and `(*)` notes; labour has
760 physical rows and more than 400 trailing empty rows. The ICT PDF's first page
has duplicate glyphs. The methodology PDF includes a blank second page.

The CPI basket is a **real XLSX**, despite the API advertising `xls` and serving
an XLS media type. The download script recognizes the OOXML directory names
without extracting archive contents. This corrects the initial plan's assumption
that XLSX files had not been observed.

### Refreshing explicitly

```bash
python -m pip install -e '.[dev,fixtures]'
# Network required; downloads only the reviewed sources, at least 6 seconds apart
.venv/bin/python scripts/fetch_fixtures.py --output /tmp/tuik-fixture-refresh
# Resume an interrupted refresh using matching source IDs/titles and SHA-256 values
.venv/bin/python scripts/fetch_fixtures.py --output /tmp/tuik-fixture-refresh --resume
.venv/bin/pytest tests/test_file_corpus.py
```

`sources.json` resolves fresh links by bulletin ID and exact file title, or an
exact publication title from a fresh search. Ambiguous matches fail; the housing
selection deliberately uses a unique title. The manifest's opaque download URLs
are historical provenance, not permanent file identities. Review new content
and goldens before replacing committed snapshots. No golden generator is run in
CI, and tests never download files. CI installs the optional `fixtures` extra to
check all source observations; base installs still check hashes/provenance and
skip reader-dependent checks.
