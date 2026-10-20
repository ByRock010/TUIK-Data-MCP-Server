# TÜİK sources and endpoint investigation

Inspected 2026-10-13 (Europe/Istanbul). Every runtime endpoint below was discovered
in first-party HTML or JavaScript and then requested directly. No guessed data
endpoints or production fixtures are used.

## Discovery and access

1. `GET https://www.tuik.gov.tr/`: official institutional HTML homepage. Links to
   `https://veriportali.tuik.gov.tr/tr`, `https://nip.tuik.gov.tr/`,
   `https://siniflama.tuik.gov.tr/` and `biruni.tuik.gov.tr` establish provenance.
   It is an investigation source, not queried during normal tool calls.
2. `GET https://veriportali.tuik.gov.tr/`: JavaScript app shell; no search records
   in server-rendered HTML. Its current asset chain was
   `assets/index-DHVaePSb.js` → `assets/main-C0Z3qMtl.js`, with dynamically imported
   search, press, themes and dataflow modules. Hashes are evidence only; runtime
   does not depend on or download these bundles.
3. `GET https://siniflama.tuik.gov.tr/`: HTML classification application. Its
   `js/uiscriptler/siniflamalar.js` sends users to
   `/Classifications/ClassificationsTurDetay?tur=0&searchname=nace`. That HTML
   defines a JSON request to `GetSiniflamalarSahibi` and edition links to
   `ClassificationsSatir?surumId=...`.
4. The official portal's embedded SDMX documentation at
   `https://veriportali.tuik.gov.tr/tr/sdmx-web-service-documentation` describes
   `https://nsiws.tuik.gov.tr/rest/dataflow/TR/all/latest?detail=full` and a Bearer
   token obtained from TÜİK sign-in. An anonymous request returned **401**.
   Authentication is outside this MVP, so `nsiws` is not a runtime allowed host.

The portal's JSON endpoints initially returned 403 (`Erişim engellendi`) with
curl's default User-Agent, even with normal Referer/Origin/XHR headers and homepage
cookies. They returned 200 with
`Mozilla/5.0 (compatible; tuik-mcp/0.1; public statistical research)`.
The implementation identifies itself this way and sends
`X-Requested-With: XMLHttpRequest`, as the frontend does. Cookies or tokens are
not required for the verified public operations. No access-control bypass or
credential acquisition is implemented; 401/403 remain explicit errors.

## Runtime endpoint inventory

All portal API paths below are relative to `https://veriportali.tuik.gov.tr`.
They are official-hosted **frontend-internal** endpoints, with no stability
contract discovered. The implementation isolates them in `TuikPortal`.

| URL | Purpose / method | Parameters | Response | Limitations |
| --- | --- | --- | --- | --- |
| `/api/tr/data/search` | Search; **POST**, read-only | JSON `text`, one-based `page`, `typeIds`, `categoryIds`, `subCategoryIds`, `years`, `levels`, `archive`, `autoFilter` | `{data:{data:[...],total,...},isError,message}` | MVP sends empty filters, `archive:false`, `autoFilter:false`; query ≤200 chars, max five pages. Ordering is upstream relevance. |
| `/api/tr/press/{id}` | Bulletin detail; **GET** | Numeric ID from `/tr/press/{id}` | `{data:{id,title,period,date,content,tables,statisticalTables,reports,metadatas,...},isError,message}` | HTML content/metadata fragments are stripped to plain text. No dates or dimensions are inferred. |
| `/api/tr/data/statistical-themes` | Current category tree; **GET** | None | Envelope containing nested `id`, `name`, `children`, `icon`, `url` nodes | MVP exposes major root categories only and caches six hours. |
| `/api/tr/dataflows` | Public export catalog; **GET** | None | Bare JSON array: `id`, `name`, `version`, optional description/footnotes/category/updatedAt, `downloadable` | Used by `search_tuik_datasets` and resource detail. Query/theme/export filtering is local, not invented upstream parameters. Cached for one hour. |
| `/api/tr/dataflows/{id}/file/{format}` | Public export; **GET** | Catalog ID e.g. `DF_ALTINSIZ_DTH+V1.0` (percent-encoded); format `csv`, `json`, `xml` | CSV verified live as UTF-8 BOM, semicolon-separated, `text/csv`; JSON/XML are advertised links | CSV only parsed. JSON/XML format options were observed in `DataflowCards-B04ONG0Q.js` and are exposed as unverified advertised links. Only downloadable catalog entries qualify. |
| `/api/tr/data/downloads` | Official tables/documents; **GET** | Opaque `t`, `p`, and optional `pid` exactly as returned in search or bulletin detail | XLS verified as `application/vnd.ms-excel` with OLE file signature; may advertise other documents | Never construct encrypted `p` values. No universal spreadsheet ingestion. File signatures/media types and response size checked. Throttled: see below. |

### Download throttling

Observed 2026-10-17. `/api/tr/data/downloads` limits back-to-back requests. Instead of
an HTTP error it returns **HTTP 200, `text/html`, about 1 KB**: a page titled
`Yönlendiriliyor...` with the text *"… saniye sonra tekrar dosya indirebilirsiniz"* and
a countdown in its script (`let time = 5;`) after which the page reloads.

- All download links of two bulletins fetched back to back with 0.5 s spacing:
  17 of 20 requests returned this page.
- The same kind of requests with 6 s spacing: 7 of 7 returned the XLS file.
- Dataflow exports (`/api/tr/dataflows/{id}/file/csv`) were not throttled: three
  different exports back to back all succeeded.

`TuikPortal` therefore downloads from this endpoint one file at a time, at least
`TUIK_DOWNLOAD_INTERVAL_SECONDS` (default 6) apart. When the throttle page still
appears, it waits for the advertised countdown plus one second (bounded to 1–60 s) and
retries within the attempt budget; persistent throttling is reported as
`download_throttled`. A reduced copy of the page is in
`tests/fixtures/download_throttle.html`.

Search fields were observed in `main-C0Z3qMtl.js`, search rendering/type labels in
`Search-BKFeVgwd.js`, detail calls in `press-WcRk5lkh.js`, category requests in
`StatisticalThemes-rmgBB3LK.js`, and export paths in `dataflows-CWs-fWWZ.js`.
The frontend advertises CSV, XML and JSON formats in `DataflowCards-B04ONG0Q.js`.

Search type mapping: 1 bulletin, 2 table (or dataset when `icon=dataflow`),
3 metadata, 4 report mapped to publication, 5 dataset, 6 publication, 0 unknown.
`databrowser2.tuik.gov.tr` URLs returned by search establish that additional
official host. Its `TR,<code>,<version>` fragment maps to the catalog's
`<code>+V<version>` identifier; no browser page is fetched to get observations.

Classification endpoints are relative to `https://siniflama.tuik.gov.tr`,
isolated in `ClassificationClient`:

| URL | Purpose / method | Parameters | Response / status | Limitations |
| --- | --- | --- | --- | --- |
| `/Classifications/GetSiniflamalarSahibi` | Search editions; **GET** | `tur=0` (all types), URL-encoded `searchname` | Bare array with `Id`, `Ad`, `Kisa_Ad`, owner fields; official frontend-internal JSON | Server does matching. MVP bounds results to 50; does not fetch code-level trees. |
| `/Classifications/ClassificationsTurDetay` | Search provenance page; **GET** | `tur=0`, `searchname` | Official public HTML page | Returned as the collection source; not scraped at runtime. |
| `/Classifications/ClassificationsSatir` | Edition provenance page; **GET** | Numeric `surumId` from JSON | Official public HTML page | Returned as the record source; not fetched at runtime. |

## Source links versus network access

`/tr/search?q=...`, `/tr/press/{id}` (optionally `/metadata`), and
`/tr/statistical-themes` are human-facing provenance URLs. Dataflows also link to
the official `databrowser2` page. The central exact-host allowlist is in
`src/tuik_mcp/config.py`; `nip`, `biruni`, and `databrowser2` were verified through
the first-party links/responses above. Legacy HTTP source links can be displayed,
but the HTTP client only fetches HTTPS. No user-supplied URL is fetched. Other
official-looking subdomains and external publications are omitted until verified.

## Investigated but unused

- `GET https://veriportali.tuik.gov.tr/config.json`: public frontend configuration;
  no runtime dependency.
- `GET https://veriportali.tuik.gov.tr/api/tr/dataflows/DF_ALTINSIZ_DTH%2BV1.0/metadata`:
  observed frontend metadata route but returned `Sayfa bulunamadı` for this record.
  It is **not implemented**; catalog metadata is used without fabricated dimensions.
- `GET https://data.tuik.gov.tr/`: redirects to the current portal. No legacy
  endpoints are assumed.
- `GET https://www.tuik.gov.tr/Home/HaberBultenleriPartial`: homepage bulletin HTML
  feed, discovered in homepage JavaScript and fetched. Too limited for full search.
- `GET https://www.tuik.gov.tr/Home/BilgiBankKategoriPartial`: homepage information
  bank cards, not the statistical category catalog. Not used.
- `https://tuikws.tuik.gov.tr/ws/TUIKSS?wsdl`: official Classification Server footer
  links a web service. Web fetch failed; no SOAP adapter or stability claim made.

Raw research downloads, if present under `docs/research`, are ignored by version
control and never loaded by runtime code. Small test fixtures are isolated in
`tests/fixtures` and do not provide production fallback data.

## Dataset discovery follow-up, 2026-10-17

The existing public `GET /api/tr/dataflows` endpoint was rechecked: 510 catalog
records, 342 with `downloadable: true`. These counts are observations, not runtime
constants. The adapter validates catalog IDs, titles, major category names and
boolean export flags; malformed responses fail explicitly without populating the
cache. Dataset search uses the normalized title, description and major category.
Category children are not indexed in this increment. No new source endpoint or
allowed host was added.

## Bulletin dataflow links (2026-10-18, #26)

`/api/tr/press/{id}` advertises dataflow tables under both `tables` and
`statisticalTables`, with `type: "dataflow"`. Observed forms include:

```text
https://databrowser2.tuik.gov.tr/#/tr/tuik/TR,DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C,1.0
https://databrowser2.tuik.gov.tr/#/tr/tuik/categories/9/9_4/TR,DF_SATIS_SEKLI_SATIS_DURUMU_V3,1.0
https://databrowser2.tuik.gov.tr/#/tr/tuik/categories/8/8_1/8_1_4/TR,DF_UCRETLI_CALISAN_ISTATISTIKLERI_C,1.0
```

The last component maps `TR,<CODE>,<version>` to `flow:<CODE>+V<version>`.
The optional category path retains every level, including `["8", "8_1", "8_1_4"]`;
it is not inferred from text. Only HTTPS links on the exact databrowser host with
recognized fragment structure are accepted. Duplicate flow IDs across the two
lists are collapsed, preferring an available category path. Source titles are
stripped of HTML. Other file downloads are preserved separately.

The service matches these IDs against the cached public export catalog. Formats
and `downloadable` are advertised catalog facts, not a download verification.
Absent entries remain listed with no formats and `downloadable: false`.
Related records retain their official databrowser URLs and the catalog lookup's
retrieval timestamp. No export is downloaded during metadata discovery.
If catalog retrieval fails, the bulletin remains available with its file links
and an explicit note that related availability is unconfirmed. That degraded
result is not cached. Only usable related datasets short-circuit the existing
file-download path; otherwise XLS/PDF links remain accessible.

Live on 2026-10-18, `press:58347` (*Konut ve İş Yeri Satış İstatistikleri, Eylül
2026*) exposed eight dataflow links. The reduced official response is in
`tests/fixtures/press_dataflows.json`. The live test follows the first downloadable
ID to CSV rows. The original `press:57987` fixture also covers the older URL form.

## Download URL stability and identities (#27, 2026-10-20)

Download URLs are `/api/tr/data/downloads?t=<list>&[pid=<bulletin>&]p=<opaque>`.
`t` names the list: `t` bulletin table, `i` statistical table, `r` report,
`y` publication. Observations on 2026-10-20 14:44 UTC:

- **`p` is not stable.** 15 of 15 files recorded 1–3 days earlier (12 fixture files
  from 2026-10-18 08:08 UTC, 3 search tables from 2026-10-17) were still listed
  under the same title, with unchanged `t` and `pid`, but every `p` had changed.
  Two requests seconds apart return the same `p`.
- **Bulletin files repeat titles across lists.** 45 random bulletins list 365 files
  (`t`: 197, `i`: 167, `r`: 1). 41 share a title with another file of the same
  bulletin; the same title appears as a bulletin table (`t=t`) and as a statistical
  table (`t=i`), which are different files (e.g. *Hizmet Ciro Endeksi* in
  `press:54133`: 58 rows versus a 215-row series). Title plus `t` leaves 4
  repeats: one statistical table listed twice in each of two bulletins.
- **Standalone search results.** Type 2 lists 2,624 items: 510 dataflows and 2,114
  downloads (`t=i`, no date field). 1,149 of these downloads share their title with
  another item (510 titles, mostly pairs); 4 of 4 sampled pairs were byte-identical
  files. Type 3 (125 metadata pages) links `/tr/press/<id>/metadata`. Type 4 has 23
  reports (`t=r`) and type 6 130 publications (`t=y`); 2 titles repeat in each.
- **Search finds titles again.** 40 of 40 sampled exact titles were on the first
  result page with the type filter. The search ignores case and the ı/i difference
  (`coicop`, `coıcop` and `COICOP` return the same results).
- **Search rate limit.** Continuous search requests at 0.5 s spacing received HTTP 429
  after about 90 seconds; about 430 requests at 2 s spacing received none. Bulk
  listing (#15) needs at least 2 s spacing and a cool-down on 429.

The resulting ID scheme (ADR 0003) derives file IDs from the exact title: bulletin
files as `press:<id>:file:<key>` with `t` as discriminator, downloadable search
results as `table:`, `report:` or `publication:<key>`. With those IDs, 30 of 30
sampled tables, reports and publications and the files of `press:58298` resolved
in fresh server sessions.

