# Metadata crawl response fixtures

Official public JSON, reduced by selecting records/fields. Source text is untrusted
data, never runtime instructions or production fallback material.

- `listing.json`: `/api/tr/data/search`, POST empty text, page 1, `typeIds: [1]`,
  non-archive; captured 2026-10-26. Ten real records; upstream reported 1,485.
- `flows.json`: four records from `/api/tr/dataflows`, captured 2026-10-26,
  including the three labour dataflows linked by the existing bulletin fixture.
- `themes.json`: reduced paths from the official statistical-theme tree captured
  during the 2026-10-13 investigation; retains category IDs/names and the source's
  press URL for `press:57987`.
- `poultry.json`: selected fields from `/api/tr/press/58058`, captured during the
  2026-10-13 investigation. HTML, metadata and previous-release links are retained.
- `../press.json`: existing official labour bulletin (`press:57987`).

The fake source deliberately places two real bulletin records on two one-record
pages with a controlled total of two. It tests pagination/failures without claiming
those are live page contents. The adapter test separately reads the recorded real
ten-record listing. No observation values or relevance judgments are invented.
