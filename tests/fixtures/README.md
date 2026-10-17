Fixtures are reduced snapshots of public TÜİK responses fetched on 2026-10-13.
Search items, catalog records and CSV rows retain upstream values. Search filter
aggregations and most bulletin body/metadata are removed to keep tests small.
The synthetic XLS signature used in conftest.py exists only in tests; live tests
verify the actual official download. Production code never reads these files.

`download_throttle.html` is a reduced copy of the HTML page that
`/api/tr/data/downloads` returned to back-to-back downloads on 2026-10-17.
