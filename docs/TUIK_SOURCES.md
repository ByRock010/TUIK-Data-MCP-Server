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
| `/api/tr/data/downloads` | Official tables/documents; **GET** | Opaque `t`, `p`, and optional `pid` exactly as returned in search or bulletin detail | XLS verified as `application/vnd.ms-excel` with OLE file signature; may advertise other documents | Never construct encrypted `p` values. No universal spreadsheet ingestion. File signatures/media types and response size checked. |

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
