# ADR 0003: Stable identities for bulletin files, tables and publications

Status: accepted. Date: 2026-10-20. Issue: #27.

## Context

`press:` (bulletins) and `flow:` (datasets) IDs are stable because they come from
TÜİK's own numeric bulletin IDs and dataflow codes. Files are different: every table,
report and publication is served from `/api/tr/data/downloads?t=…&p=…` with an
opaque, apparently encrypted `p` value that TÜİK regenerates over time. Until now
these files got `resource:<sha256(url)>` handles that only work inside one server
session. The search index (#14, #22), the relevance judgments (#11) and file
selection in `get_tuik_data` (#6) all need IDs that still resolve in a fresh session.

## Evidence

See "Download URL stability and identities" in `docs/TUIK_SOURCES.md`:

- `p` changed for 15 of 15 files within 1–3 days; `t`, `pid` and titles did not.
- In 45 bulletins (365 files), 41 files repeat a title; the bulletin table (`t=t`)
  and the statistical table (`t=i`) with the same title are different files.
  Title + `t` leaves 4 repeats (a statistical table listed twice).
- 1,149 of 2,114 downloadable type-2 search results repeat a title; sampled
  repeats were byte-identical files. Search results carry no date.
- The portal search found 40 of 40 exact titles on its first page and ignores
  case and ı/i.

## Decision

File IDs are derived from the exact title, which is the only stable, visible field:

| Kind | ID | Discriminator in the digest | Resolution |
| --- | --- | --- | --- |
| Bulletin file | `press:<id>:file:<key>` | download list `t` (`t`, `i`, `r`) | Re-fetch `/api/tr/press/<id>`, match the link's ID |
| Standalone table | `table:<key>` | search type `2` | Portal search for the slug, type filter `[2]` |
| Report | `report:<key>` | search type `4` | Portal search, type filter `[4]` |
| Publication | `publication:<key>` | search type `6` | Portal search, type filter `[6]` |

`key` = `<slug>~<digest>[.<ordinal>]`:

- **slug:** the title in lowercase Turkish (`turkish_casefold`), non-letters replaced
  by `-`, other accented letters reduced to their base letter, cut at a word boundary
  at 80 characters. It keeps IDs readable and is the search text for resolution.
- **digest:** the first 10 hex digits of `sha256(title + "\x1f" + discriminator)`
  over the exact, whitespace-normalised title. It is case-sensitive, so
  *Cinsiyete Göre Genç İşsizlik Oranı* and *Cinsiyete göre genç işsizlik oranı*
  get different IDs.
- **ordinal:** `.2`, `.3`, … for later bulletin files with the same title and `t`,
  in the bulletin's list order. The first occurrence has no ordinal. Search results
  never get ordinals: repeated titles there were the same file, so the first match
  is used (the resolver still accepts `.N` and takes the N-th match).

`flow:`, `press:`, `press:<id>:metadata` and `classification:` stay unchanged.
Anything else, e.g. links to other TÜİK sites, keeps a session-only `resource:`
handle, documented as such. Only stable IDs may be indexed, judged or presented
as reusable.

Resolution checks the in-session search cache first. A bulletin file is resolved
from the bulletin's current file list (one request, cached for an hour). A search
kind is resolved by searching for the slug with the type filter and taking the
`ordinal`-th result whose computed ID matches, reading at most five result pages.
No match returns `not_found` with the bulletin page or a search URL to retry from.

## Consequences

- An ID names *the current file with this title*, not a byte snapshot. When TÜİK
  republishes a table under the same title (e.g. *İşsizlik Oranı* every month), the
  ID follows the update; content identity is the file's SHA-256 (cache, #9).
- Renaming a file on TÜİK's side breaks its ID (`not_found`). Titles that carry a
  period ("…, Ağustos 2026") change by design and identify different editions.
- Repeated search titles resolve to the first match; this is safe only while such
  repeats stay identical files, as observed. Bulletin file ordinals rely on the
  bulletin's file order.
- No database is needed: IDs are self-describing, which keeps the server stateless
  (the search index is decided separately, in ADR 0002 under #13).

## Alternatives considered

- **Hash of the download URL** (`resource:`): breaks whenever `p` changes.
- **Decoding `p`:** it is opaque; constructing or reverse-engineering it is out of
  bounds (`docs/TUIK_SOURCES.md`).
- **Position in a bulletin's file list:** unreadable and silently points to another
  file if TÜİK inserts one.
- **Persisted ID→URL mapping:** needs a store and a crawler; title-derived keys
  resolve without either.
