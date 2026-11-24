# ADR 0007 · Regional comparisons and boundary eligibility

**Status** · Proposed for review. **Audit date** · 2026-11-23 (Europe/Istanbul).
**Tracking** · [Research #106](https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/106)
and [choropleth #107](https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/107).

## Decision

Support a bounded **table comparison** at province level for the three annual
series below. Use the official İBBS level 3 code and label. Defer the #107 map
pilot until an attributed, dated boundary source has explicit reuse terms for
our intended delivery and passes geometry QA. The CIP province layer joins all
81 provinces, but has four invalid features and no declared boundary vintage.
Map acceptance requires evidence on all three points.

The inputs tested were public HTTPS responses and required no account, key or
project-hosted service. A local or server-side table comparison needs only the
allowlisted TÜİK sources. A future map can render locally from a permitted,
versioned vector snapshot without a tile or hosting service. The tested CIP
layer is ineligible for that snapshot.

## Scope and inputs

The audit uses the TÜİK Coğrafi İstatistik Portalı (CIP) menu and its own
`GetMapData` request shape. The menu advertised 80 entries on the audit date.
These internal frontend paths have no observed stability commitment. Obtain
indicator IDs, allowed levels and `kaynak`, `kayitSayisi` and `period` from the
menu. Restrict probes to the sampled IDs and levels. The source returned HTTP
200 for all inputs listed below. Byte sizes and SHA-256 hashes are in the dated
[evidence and 81-row crosswalk](../verification/issue-106/evidence-2026-11-23.json).

| Local input | First-party source or lookup |
| --- | --- |
| `sideMenu.json` | [CIP menu](https://cip.tuik.gov.tr/assets/sideMenu.json?v=2.000) |
| `nuts2.json`, `nuts3.json` | [CIP level 2](https://cip.tuik.gov.tr/assets/geometri/nuts2.json), [CIP level 3](https://cip.tuik.gov.tr/assets/geometri/nuts3.json) |
| `ibbs.json` | [İBBS 2005 edition 164, complete hierarchy](https://siniflama.tuik.gov.tr/Classifications/GetSiniflamaSatir?surumId=164&seviye=0&kod=0) from the [classification edition](https://siniflama.tuik.gov.tr/Classifications/ClassificationsSatir?surumId=164) |
| `population.json`, `population-l2.json` | [CIP province population](https://cip.tuik.gov.tr/Home/GetMapData?kaynak=medas&duzey=3&gostergeNo=ADNKS-GK137473-O29001&kayitSayisi=5&period=yillik), [level 2 population](https://cip.tuik.gov.tr/Home/GetMapData?kaynak=medas&duzey=2&gostergeNo=ADNKS-GK137473-O29001&kayitSayisi=5&period=yillik) |
| `housing.json` | [CIP province house sales](https://cip.tuik.gov.tr/Home/GetMapData?kaynak=ilGostergeleri&duzey=3&gostergeNo=INS-GK055-O006&kayitSayisi=5&period=yillik) |
| `unemployment-l3.json`, `unemployment-l2.json` | [CIP province unemployment](https://cip.tuik.gov.tr/Home/GetMapData?kaynak=medas&duzey=3&gostergeNo=ISGD-GK18575-O18504&kayitSayisi=5&period=yillik), [level 2 unemployment](https://cip.tuik.gov.tr/Home/GetMapData?kaynak=medas&duzey=2&gostergeNo=ISGD-GK18575-O18504&kayitSayisi=5&period=yillik) |
| `population.xls` | **İllere göre il/ilçe merkezi, belde/köy nüfusu ve yıllık nüfus artış hızı** in release 53899, 2026-02-09. [ADNKS bulletin](https://veriportali.tuik.gov.tr/tr/press/53899). |
| `labour.xls` | **İl düzeyinde işsizlik oranı** in release 57996, 2026-03-25. [Annual and regional labour bulletin](https://veriportali.tuik.gov.tr/tr/press/57996). |

For the two XLS files, resolve the bulletin by `GET /api/tr/press/{id}` on
`veriportali.tuik.gov.tr` and take its exact-title link from `tables` or
`statisticalTables`. Follow the returned URL with the existing audited
[portal headers and download pacing](../TUIK_SOURCES.md). Download links can
rotate. The retrieval date and hashes identify the observed snapshots.

## Geography and join results

The official hierarchy has 1 national code (`TR`), 12 İBBS level 1 regions,
26 level 2 regions and 81 level 3 provinces. The Classification Server's raw
`duzey` uses 1 for the country and 4 for provinces. CIP uses `duzey=3` for
provinces, exposes unpadded `duzeyKodu` in its indicator rows, and gives the
canonical `nutsKodu` and parent `bolgeKodu` in its geometry properties. At
level 2, CIP `duzeyKodu` is already an İBBS code. An administrative district
(`ilçe`) is below province. Its identifiers and boundaries are separate. The
population menu's 4 and 5 entries are outside this audit.

- **Province completeness.** CIP level 3 geometry, TÜİK edition 164 and the
  labour workbook cover the same 81 İBBS level 3 codes, with no duplicate,
  missing or unmatched province codes. All 81 CIP parent codes match the
  official level 2 parent. The workbook's `TR` national row is kept separate.
  CIP's population, housing and labour province series each contain those
  same 81 numeric geography keys. The full crosswalk includes e.g. `34 →
  TR100 → İstanbul`. Numeric codes are strings with an explicit CIP namespace.
- **Labels.** After Turkish-aware case normalization, CIP labels match the
  official classification in 79 of 81 cases. The two discrepancies are
  `TR714` (`NEVSEHİR` vs `NEVŞEHİR`) and `TR721` (`KAYSERI` vs `KAYSERİ`).
  Both match by code and parent, and the labour worksheet independently
  gives the official spelling. Use canonical labels from the edition. Retain
  both raw spellings in evidence. Any other ambiguous or unmatched key fails
  the complete comparison rather than receiving a guessed province.
- **Level 2 check.** The CIP level 2 geometry and both sampled level 2 series
  have all 26 codes. Provincial population sums match every CIP level 2
  value for 2021–2025 (130 comparisons). An unemployment rate requires its
  source-published regional estimate. A mean or sum of provincial rates
  cannot substitute for it.

## Audited indicator coverage

| Indicator | Audited geography and periods | Evidence and limits |
| --- | --- | --- |
| ADNKS end-of-year resident population, persons | 81 provinces and 26 level 2 regions, 2021–2025 | All 162 provincial 2024/2025 values match the bulletin's sheet `t1`, columns `B:C`, with province names resolved against the official code list. The 2025 provincial sum is 86,092,168. The bulletin documents annual end-of-year counts and changes in administrative divisions. Monthly comparisons and district joins are outside scope. [ADNKS metadata](https://veriportali.tuik.gov.tr/tr/press/53899/metadata). |
| Residential sales, counts | 81 provinces annually, 2021–2025 in CIP | The five provincial sums equal the **post-revision** national house-sales totals in Table 2 of the revision released with bulletin 58340. For 2025 the old total is 1,688,910 and the revised total is 1,760,292. Store the release vintage. The portal separately advertises monthly province data since 2013. Its full CSV exceeded this audit's 10 MiB limit, so monthly rows are excluded. Level 2 and district totals are outside the audited series. [Revision report, Table 2](https://veriportali.tuik.gov.tr/api/tr/data/downloads?p=bfDCSkHrLOZKFgfdnQSewLEIe%2FqAxoZ0v%2Ff%2B%2FYjygnL9tKGACzjLCsCmNU6cYviYppSqXuDtWBm%2B7mVB35TCSSBOf6KKeentbbPOKR2uGms0Np%2B6sdYGJJZ40TlBh%2BVl&pid=58340&t=r) · [Province dataflow](https://veriportali.tuik.gov.tr/tr/databrowser/tuik/TR%2CDF_SATIS_SEKLI_DURUMU_ILILCE_V3%2C1.0). |
| Unemployment rate (ages 15+), percent | 81 provinces annually, 2022–2025. CIP level 2 has 26 regions, 2021–2025. | All 324 provincial CIP rates equal the XLS estimates rounded to one decimal. Use the XLS `Tablo` with 95% confidence intervals and the published caution flag for reporting (10, 6, 7 and 9 flagged provinces across 2022–2025). Annual province 2021 and 2026 are absent from the observed workbook. Monthly province and district rates are unsupported. Do not transfer national monthly estimates to regions. [Annual and regional bulletin](https://veriportali.tuik.gov.tr/tr/press/57996) · [Historical methodology](https://veriportali.tuik.gov.tr/tr/press/53838/metadata). |

The first table profile accepts one listed indicator, one audited annual
period and at most 81 province codes. It returns the exact values and unit,
source URL, reference period, snapshot/release vintage and missing status. For
unemployment it also returns the confidence interval and caution flag. Keep
national totals as reference rows. Output outside the audited level, period or
measure is explicitly unsupported. Record the selected source and any revisions
before comparing across years or combining population denominators with counts.

## Boundary and reuse contract

The tested province file contains 81 `MultiPolygon` features, 10,157 positions
and 233,221 bytes. The file declares OGC CRS84 (longitude, latitude). Its
contents give neither a boundary year nor a geometry-specific licence. The
retrieval date and İBBS classification edition establish snapshot and code
provenance. Geometry vintage remains unknown. GEOS 3.14.1 finds self-
intersecting rings for İstanbul `TR100`, İzmir `TR310`, Muğla `TR323` and Sinop
`TR823`. Raw coordinates stay in the local audit input. The committed evidence
contains source identifiers, counts, hashes and validity diagnostics.

The institutional legal notice allows reuse of published data with attribution.
The CIP frontend also names optional third-party basemap layers. That attribution
does not establish the rights holder or redistribution terms for its separate
province vector asset. Require source confirmation for bundled geometry and
exported maps, including permitted simplification and public redistribution.
GISCO is a dated alternative to evaluate separately. Its geographic data has
specific terms and attribution requirements. Do not assume commercial rights
or treat its boundaries as interchangeable with CIP. [TÜİK legal notice](https://www.tuik.gov.tr/Kurumsal/Yasal_Uyari) · [GISCO terms](https://ec.europa.eu/eurostat/en/web/gisco/geodata/statistical-units).

To reconsider #107, require one explicit boundary vintage, declared coverage
date compatible with the observation period, source/licence, original and
processed hashes, CRS and code scheme. Limit the candidate to level 3, 81
features, 25,000 coordinate positions and 512 KiB uncompressed GeoJSON. Validate
coordinate order and ranges, ring closure, finite values, non-empty polygons,
GEOS validity, complete code coverage and shared edges and overlaps. Default
to the original geometry when it meets these limits. If simplification is
needed, use a topology-preserving shared-edge method in a recorded metre-based
projection. Record tolerance and revalidate after conversion to CRS84. Reject
features and snapshots that fail these checks. Keep the same exact-value table
when geometry is unavailable.

## Reproduction

Save the endpoint responses under the names in the input table. Use a 2 MiB
per-response cap for this exact audit and at least one second between CIP
requests. The existing portal adapter spaces XLS downloads by at least six
seconds. The audit script is offline. It uses the project's `xlrd` and an
isolated `shapely==2.2.0` install for geometry QA.

```sh
python docs/verification/issue-106/audit.py /path/to/local/inputs > /tmp/regional-coverage.json
cmp /tmp/regional-coverage.json docs/verification/issue-106/evidence-2026-11-23.json
```

The comparison is byte-exact for the same source bytes and GEOS version. If a
source has changed, retain and review the new hashes, join results and
coverage differences before updating the ADR.
