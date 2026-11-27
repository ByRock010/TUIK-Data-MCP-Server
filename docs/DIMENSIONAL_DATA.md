# Audited DataBrowser queries

`query_tuik_dimensions` is an opt-in pilot for six exact DataBrowser flow
versions and the selections approved by [ADR 0008](adr/0008-dimensional-data-access.md).
The service uses TÜİK's public frontend-internal endpoints. Version or
contract changes can interrupt the pilot. The public CSV and workbook
tools remain the default path for other data.

1. Call `query_tuik_dimensions` without arguments to list supported resource
   IDs, audited periods and required code filters.
2. Pass one resource ID to inspect the DSD version and dimension order.
   Pass `dimension` to page that dimension's codes and Turkish/English
   labels. The same lookup can resolve coded observation attributes.
3. Pass the audited `period` and all required `filters`. Additional codes
   observed in the audit can narrow a selection. Code and codelist identity
   are validated against the current DSD before the upstream call.

For the general seasonally adjusted CPI index in the reviewed series:

```json
{
  "resource_id": "flow:DF_TUFE_SDMX_TT05+V1.0",
  "period": "2026-10",
  "filters": {
    "OZEL_KAPSAM_TUFE": ["GT_1"],
    "DEGISIM": ["1"]
  }
}
```

The same criteria reach the count and CSV endpoints. The count must be a
known integer from 0 to 200 before the server downloads a CSV. An explicit
zero returns `empty` with no CSV request. A missing count means the upstream
could not confirm the selection. No broad export is downloaded as a
fallback. Count/row mismatches, unaudited keys, duplicates, shape changes
and unexpected codes fail the selection.

The response records the flow and DSD identity, structure hash, verified
row count, downloaded CSV bytes, retrieval time and snapshot ID. Observations
use the [observation contract](OBSERVATIONS.md) for exact decimals, period,
one-based CSV record locators and source flags. Dimensions constant across
the complete selection appear once in `constant_dimensions`. Empty values
and restrictive confidentiality flags are distinct. The original
source attribute codes are preserved. Unit, measure, geography and adjustment
semantics require reviewed recipes. A code's presence in a codelist alone
does not establish statistical coverage.

The full selection is validated before paging. Pass `next_offset` and
`snapshot_id` with the same filters to continue reading observations.
A changed payload requires restarting at the first page. `complete` means the
response's upstream count and selection were verified. Page size is indicated
separately by the returned observation count and `next_offset`.

## Operating bounds and errors

The worker uses the same HTTP pacing, timeout and bounded retries as the other
official tools. DSDs are cached per exact flow for one hour. Requests cap
decoded DSD responses at 3 MiB and count/CSV responses at 256 KiB, subject
to a lower operator HTTP cap. The output uses the configured query byte budget.
The profile can be widened only with new recorded evidence.

HTTP authentication challenges, forbidden responses, missing upstream flows,
timeouts and rate limits have distinct error codes. An unknown count or
changed schema is reported separately from a zero-row result. The pilot
has no credentials or authenticated SDMX path. Any official authenticated
profile requires separate access provisioning and live verification.
[Official TÜİK SDMX documentation](https://veriportali.tuik.gov.tr/en/sdmx-web-service-documentation).

The tests use compact DSD projections and selected CSVs captured from the
public DataBrowser, with the exact dimension order, source attributes and
multilingual labels preserved for the observed keys. Fixture CSVs use Unix
line endings. The recorded [independent labelled CSV audit](verification/issue-104/evidence-2026-11-23.json)
is the parity baseline for all 89 observations.

In the paced live check, the October CPI profile returned 2 rows and 405 decoded
CSV bytes. Adding `DEGISIM=1` reduced the upstream count to 1 and the CSV to 309
bytes. Its index level, 140.0811924821, matched the independent labelled audit.
