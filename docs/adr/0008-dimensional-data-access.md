# ADR 0008 · Bounded dimensional data access

**Status** · Proposed for review. **Audit date** · 2026-11-23 (Europe/Istanbul).
**Tracking** · [Research #104](https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/104),
[adapter #105](https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/105),
[observation model #87](https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/87).

## Decision

Approve a **key-free, opt-in DataBrowser pilot** for #105 once #87 supplies the
observation contract. Start with the six exact dataflow versions and verified
selections below. Grow period and filter coverage only with recorded parity
and bounds checks. Preserve the current public export tools as the default.
Defer the authenticated SDMX adapter until a credentialed, separately authorized
comparison demonstrates additional verified coverage or a supported operating
advantage. A catalogue entry alone is insufficient evidence of coverage.

This decision is supported by 89 exactly equal overlapping values, matching
available observation attributes, fewer selected rows in all six domains, and a
bounded industry response for a full export that exceeded the current default
size limit. Two extra historical CPI observations establish a coverage difference
between routes. Their values need an independent matching-vintage source before
they are eligible for reporting.

## Routes and provenance

| Route | Identity and shape | Access and support boundary |
| --- | --- | --- |
| Existing public export | `/api/tr/dataflows` on `veriportali.tuik.gov.tr` supplies catalogue IDs, versions, export flags, period labels and update metadata. `/api/tr/dataflows/{id}/file/csv` returns a labelled whole-file snapshot. | Works without a key. Local filters are applied after transfer. The response can omit constant dimensions, and a label column does not provide a codelist contract. [Existing source inventory](../TUIK_SOURCES.md). |
| Public DataBrowser | `databrowser2.tuik.gov.tr/api/core/nodes/1/datasets/TR,{code},{version}` exposes UI structure, DSD/codelist metadata, selected counts and coded SDMX-CSV. | Worked without a key on six exact versions. Official frontend-internal endpoints with no observed public stability commitment. A language setting selects labels, while values and keys keep source codes. Use `UserLang: tr` for probes. |
| Documented SDMX REST | `nsiws.tuik.gov.tr/rest` documents versioned flow, DSD, codelist and content constraint lookups. A series key follows DSD order, and `startPeriod`/`endPeriod` filter observations. SDMX-CSV and JSON-stat are documented options. | The documented `GET /rest/dataflow/TR/all/latest?detail=full` returned HTTP 401 (48 response bytes) without authorization. Catalogue, structures, attributes, request sizes, freshness and numeric parity for all six domains remain unmeasured on the authenticated route. [Official SDMX documentation, sections 4–6](https://veriportali.tuik.gov.tr/en/sdmx-web-service-documentation). |

Maintain a single public catalogue. Resolve `flow:{code}+V{version}` to the
exact `TR,{code},{version}` for the DataBrowser route. Verify the requested
`DATAFLOW` identity and the actual DSD reference, including its independent
version, on return. The public catalogue observed on 2026-11-23 contained
**523 entries**, with **341** marked downloadable. These are endpoint counts
for that date. They describe neither the accessible DataBrowser catalogue nor
an authenticated SDMX coverage estimate.

Preserve publisher, public dataset page, backend, retrieval time and response
hashes. Keep catalogue `updatedAt`, catalogue `period`, the observed
`TIME_PERIOD` and the release revision distinct. For example the ADNKS entry's
catalogue period is `2026-Q1`, while its sampled population value is for 2025.
TÜİK's site permits data reuse with attribution. The separate service's
credential provisioning and operational terms still govern authenticated use.
[TÜİK legal notice](https://www.tuik.gov.tr/Kurumsal/Yasal_Uyari).

### Authentication lifecycle

TÜİK documents two ways to obtain an access token at its official issuer.
A portal user can create an API key after account and SMS verification, or
TÜİK can provision separate client credentials. The key or credentials go to
TÜİK's token service. The short-lived access token is used as a Bearer token
only for the documented SDMX service. The audit sent no credentials and cannot
establish the actual token lifetime or renewal behavior. [Official SDMX documentation, section 4](https://veriportali.tuik.gov.tr/en/sdmx-web-service-documentation).

If a later adapter is approved, load operator-managed credentials from an
environment or secret store. Keep access tokens in process memory. Use the
returned `expires_in` if available, refresh or reacquire before expiry, and
allow at most one refresh-and-retry following 401 on an otherwise valid
request. Keep 403 distinct as a permission error and redact credentials and
headers from logs. Show `not_configured` for an explicitly requested
authenticated mode when credentials are absent. Keep authentication, key
creation and phone verification outside MCP conversations and arguments.
[OAuth token-response fields](https://www.rfc-editor.org/rfc/rfc6749#section-5.1).

## Bounded six-domain sample

Each row below is a separate fixed selection from [the probe manifest](../verification/issue-104/samples.json).
The broader count uses the same period with the extra dimension filters
removed. The selected count uses the exact criteria sent to the CSV endpoint.
Both probes count through an unrestricted `FREQ` dimension. Every selected
CSV has the reported number of rows and each row satisfies the requested
period and codes. Byte figures are decoded body bytes. Warm cost is the
selected count plus selected CSV. Cold cost also includes UI structure and
DSD. The shared catalogue and the audit-only broader count are excluded.

| Domain and catalogue ID | Period and selected codes | Broader → selected | Exact matches | Whole export bytes | Warm / cold bytes |
| --- | --- | ---: | ---: | ---: | ---: |
| Prices · `DF_TUFE_SDMX_TT05+V1.0` | `2026-10`, `OZEL_KAPSAM_TUFE=GT_1` | 50 → 2 | 2 | 124,340 | 542 / 1,587,639 |
| Labour · `DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C+V1.0` | `2026-09`, `SEX=_T` | 96 → 32 | 32 | 3,553,859 | 3,518 / 1,476,983 |
| Population · `DF_ADNKS_T30+V1.1` | `2025`, `IKAMET_YERI=TR100` | 83 → 1 | 1 | 244,402 | 585 / 1,431,610 |
| Trade · `DF_ALTINSIZ_DTH+V1.0` | `2026-09`, `DTE_SEKTOR_KODLARI=0` | 59 → 8 | 8 | 2,954,517 | 1,179 / 2,532,937 |
| Housing and workplace sales · `DF_SATIS_SEKLI_SATIS_DURUMU_V3+V1.0` | `2026-10`, `SATIS_TURU=_T` | 28 → 4 | 4 | 674,235 | 735 / 948,299 |
| Industry · `DF_SANAYI_URETIM_ENDEKS_ANA_C+V1.0` | `2026-09`, `SEASONAL_ADJUST=Y`, `DEGISIM_TUR=1` | 210 → 42 | 42 | 14,364,209 | 5,260 / 2,396,947 |

Cold metadata exceeded the whole CSV for prices, population and housing.
Cache structure by known dataflow and DSD version before relying on the warm
advantage. The industry full export was rejected at 10 MiB, then collected
once at a 16 MiB cap for independent comparison. The authenticated SDMX path
supplied no observation bytes or verified coverage for any row in the table.

### Mapping and differences

- Match each versioned DSD key to exported Turkish label columns, using the
  DSD codelist and explicit source header names. Reject a non-unique key on
  either side. Compare the two raw value strings numerically with `Decimal`
  after the declared comma/dot normalization. Check the selected keys in both
  directions. The replay stores both raw strings and any key, value or
  attribute discrepancy. All 89 common values matched in this snapshot.
- Retain all source dimensions, including constants and `_Z`, plus original
  labels and codelist names/versions. The population sample reports the actual
  province as `IKAMET_YERI=TR100` (İstanbul), while `REF_AREA=TR` records the
  broader coverage. Its 2025 count is 15,754,053. The trade sample retains
  `OLCU_BIRIMI=RO` and base period `2015`. The housing flow distinguishes the
  house and workplace indicators. Keep them separate when requesting totals.
- Preserve original and adjusted values as distinct code tuples. The labour
  sample contains `SEASONAL_ADJUST=N` and `S`. The industry selection is `Y`
  (calendar and seasonally adjusted) with base `2021`. The price selection
  carries its base `2025`, publication edition `2026_01`, and adjustment in the
  selected indicator's code. Keep the original observation text, measure and
  multiplier separate. Avoid assuming units from an index title or treating a
  blank multiplier as an asserted scale.
- Keep `OBS_STATUS`, `CONF_STATUS`, precision, transformations and other
  available attributes at the source grain. Industry returns `A` (normal)
  and `F` (free for publication), which match the display export. Preserve any
  provisional, confidential, suppressed or unknown code as received and resolve
  its label against the exact codelist. A blank or absent flag conveys unknown
  status. The audit did not observe published suppressed or provisional values.
- For `2024-01`, the same price indicator returned two observations with base
  year `2025` and publication edition `2026_01`. The public portal CSV begins
  in `2025-10`. Preserve these as two DataBrowser-only values, with source and
  vintage. The portal's absence cannot prove that the values are erroneous.
- The same price DSD contains `REF_AREA=TR100`. The selected province and
  October 2026 returned HTTP 200 with `{"criteria":[]}` and no `obsCount`.
  This establishes that metadata can advertise a code without selection-level
  coverage evidence. An absent count is **unknown**, and the probe stopped at
  preflight for this selection. [Dated evidence including both cases](../verification/issue-104/evidence-2026-11-23.json).

## Gate and contract for #105

- Expose an opt-in, bounded path with explicit resolved code filters and periods.
  Reuse existing IDs, allowlisted TÜİK hosts and HTTP pacing. Validate dimensions
  and codes against the verified versioned DSD. Require one bounded time window
  and at least one dimension filter. Ask for an alternate unfiltered dimension
  for the count probe. Reject missing, invalid or over-budget counts before CSV
  download. Treat an explicit zero as an empty selection.
- Initial caps are 200 observations, 256 KiB decoded response for view/count/data,
  and 3 MiB decoded DSD, with finite timeouts and no broad fallback download. The
  observed DSDs ranged from 940,398 to 2,523,341 bytes. Cache exact structures by
  version and hash with a one-hour revalidation window. A changed structure or
  an unknown version requires contract checks before enabling its mappings.
- Check returned identity, explicit period/codes, full key uniqueness, attributes
  and selected count. Report a consistent snapshot only on complete verification.
  A count/row mismatch, duplicate, unexpected code or partial transfer fails
  with an actionable upstream-contract error. Never silently pick a conflicting
  value or discard an inconvenient attribute. Redact bodies that carry secrets
  from diagnostics and preserve safe source locators.
- Differentiate 401/403, 404 for a missing flow, zero versus unknown selection
  counts, timeouts, 429 and malformed/oversized responses. The audit saw read or
  connect timeouts on initial DSD/portal requests and recovered on one paced
  retry. Any automatic retry must keep the criteria and size bounds. Offer the
  existing file workflow explicitly when supported, with its own provenance.
- Before wider rollout, independently verify flagged/missing-value fixtures,
  authenticated access and lifecycle if sponsored, and whether observed extra
  periods agree with a second official source of the same base and vintage.
  Stop at no-go for a new version lacking a complete DSD, a reliable bounded
  count, or consistent values. Keep recorded counts, code lists and paired
  observations as evidence for #87 and #105.

## Reproduction

Use the exact pairs and dates in `samples.json`. Save the public catalogue
response to `catalog.json`. For each domain, substitute its `{domain}` name and
use `TR,{code},{version}` from its catalogue ID in the DataBrowser base above.
Use the existing portal client headers for portal files and a Turkish label
setting for the DataBrowser calls. The probe used at least 0.65 s between calls,
with per-request timeout and decoded byte caps. It allowed up to 12 MiB for DSD
research requests and 16 MiB for the independent industry whole-file comparison.

| Local filename | Request relative to the appropriate source |
| --- | --- |
| `{domain}-view.json` | DataBrowser `GET /structure` |
| `{domain}-dsd.json` | DataBrowser `POST /download/structure` with `[]` |
| `{domain}-broad-count.json` | DataBrowser `POST /PartialCodelists/FREQ` with time criterion only |
| `{domain}-count.json`, `{domain}-selected.csv` | DataBrowser `POST /PartialCodelists/FREQ` and `POST /download/csv` with the full selection. Download only when count is known and within cap. |
| `{domain}-portal.csv` | Portal `GET /api/tr/dataflows/{id}/file/csv`, encoding `+` as `%2B` |
| `prices-historic-count.json`, `prices-historic-selected.csv` | Same price selection with `2024-01-01` to `2024-01-31` in the time criterion |
| `prices-empty-geography-count.json` | Price flow count with the October 2026 time criterion and only `REF_AREA=TR100` for the code filter |
| `anonymous-sdmx.json` | Documented unauthenticated SDMX catalogue call. Record status and error response. |

Each DataBrowser criteria body is an array. With prices as an example, the
exact submitted selection was:

```json
[
  {"id":"OZEL_KAPSAM_TUFE","type":"CodeValues","filterValues":["GT_1"],"period":0},
  {"id":"TIME_PERIOD","type":"TimeRange","from":"2026-10-01","to":"2026-10-31"}
]
```

```sh
python docs/verification/issue-104/audit.py /path/to/local/responses > /tmp/dimensional-evidence.json
cmp /tmp/dimensional-evidence.json docs/verification/issue-104/evidence-2026-11-23.json
```

The dated JSON retains both raw representations, code/attribute maps, body
sizes and input hashes. A different hash from a live request warrants a new
snapshot and review, even when values still agree.
