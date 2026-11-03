# File content index (#22)

The metadata index answers "which bulletin or dataset?". The content index answers
"which table, which page?": it holds the tables of bulletin workbooks and the pages
of PDFs, each pointing at its file and its place in it.

## Documents

| Kind | Source | Searchable text | Locator |
| --- | --- | --- | --- |
| `table` | each XLS/XLSX table (sheet) | title (TR/EN), headers (TR/EN), unit, notes, source line | `sheet` |
| `document_chunk` | each ~1,500-character passage of a PDF page, never crossing pages | page text, page heading | `page` |

Table values are not indexed: the headers and notes say what a table contains, and
`get_tuik_data` reads its rows. `parent_id` is the stable file ID from #27
(`press:<n>:file:<key>`, or `publication:`/`report:`/`table:` for portal files), and
the document ID adds only the locator: `<file>#p<page>.<n>` or `<file>#t<n>`. Both
kinds inherit the bulletin's theme, series, period and publication date, and their
description names the bulletin, so generic file titles such as *Metodolojik
açıklama dokümanı* are told apart. `source_url` is the bulletin page; download URLs
rotate and are never indexed. Schema version 2 adds `page` and `sheet` to the strict
mapping, so the first run after upgrading must be `--full`.

## Search

`search_tuik` searches content only when asked: `kind="document_chunk"` for PDF
passages, `kind="table"` for workbook tables. Without a kind, results stay datasets,
bulletins and metadata, so passages never crowd them out. A content result's `id` is
the file, with `page` or `sheet` locating the match:

```json
{"query": "CATI telefonla görüşme", "kind": "document_chunk", "limit": 3}
```

returns the labour methodology PDF's file ID with `page: 4`; open it with
`read_tuik_document(id, page=4)`. A `kind="table"` result opens with
`get_tuik_data(id, sheet=sheet)`. The ID resolves in a fresh session like any other
bulletin file. Without the index, `kind="table"` falls back to the portal's table
search and `document_chunk` returns nothing, with a warning that passages need the
content index.

## Building it

```bash
tuik-mcp-index --full --content --report /tmp/index.json            # with content
tuik-mcp-index --incremental --content --max-content-files 50       # continue
tuik-mcp-index --incremental --content-file 'publication:<key>'    # on demand
```

- **Selection.** By default the newest bulletin of each series (`series_id`)
  contributes its XLS/XLSX/PDF files, newest series first. `--content-file` adds a
  specific file on demand (any bulletin, publication, report or table file ID) and
  keeps it on later runs.
- **Throttle-aware.** Files resolve, download and parse through the server's own
  `CatalogService`: the same allowlist, file verification, six-second download spacing
  and parse budgets. At most `--max-content-files` downloads (default 25), and as
  many bulletin file-list requests, happen per run; the rest are reported as
  `pending_files`/`pending_bulletins` and continue on the next run.
- **Order.** The budget goes first to explicit `--content-file` refreshes, then to
  listed files never tried (newest series first), and only then to earlier failures,
  fewest attempts first. A file that keeps failing therefore never blocks the files
  after it; it is retried at most three times (`given_up` counts the rest) until its
  bulletin is republished, which fetches a fresh file list and starts its files
  over. `--content-file` always retries. Bulletin file lists are scheduled the same
  way: lists never fetched go before earlier failures, and a list that keeps failing
  is retried at most three times per publication (`lists_given_up`), so unavailable
  bulletins never keep the healthy ones after them out of the index.
- **State.** `--content-state` (default `.tuik-content-state.json`) keeps every
  indexed file's documents and each bulletin's file list. File IDs embed their
  bulletin, so a cached file is never downloaded again. Every run, including a
  `--full` rebuild without `--content`, re-emits the cached content, so rebuilds do
  not drop it. The state is written only after the index write succeeds; dry runs
  and failures leave it unchanged. Files of bulletins that are no longer the newest
  in their series leave the state (and, on the next full build, the index).
- **Failures.** A file that cannot be downloaded or parsed is reported under
  `content.failures`, and its attempt count is kept in the state. Indexed content is
  never dropped for lack of budget or a failed refresh: a deferred `--content-file`
  refresh (`pending_refreshes`), a failed one, or a bulletin whose file list cannot
  be refreshed keeps the previous documents. PDFs are read up to 300 pages
  (`page_limited` lists longer ones), and a file may yield at most 2,000 documents.

The MCP server never writes to the index: indexing a file read through
`get_tuik_data` or `read_tuik_document` is an explicit `--content-file` run, so the
read-only server needs no cluster write access.

## Acceptance (2026-11-02)

On a local OpenSearch 3.3.2 with the recorded labour bulletin (57987): its
methodology PDF yields 15 passages over 10 pages and its two recorded workbooks one
table each. With `kind="document_chunk"`, *CATI telefonla görüşme* returns page 4
first (the only page about CATI) and *anket referans ay ve haftaları* returns
page 9 (Ek 1) first, scoring 11.9 against 3.8. With `kind="table"`, *mevsim
etkisinden arındırılmış işgücü göstergeleri* returns the seasonally adjusted
workbook first (15.8 against 7.0), and *işgücüne katılma oranı* finds both labour
tables through their headers. Without a kind, no content is returned.

## Known limits

- Ranking is lexical. A diagram that repeats defined terms can outrank the page
  that defines them: *zamana bağlı eksik istihdam* returns the Ek 2 diagram (page
  10) before the definition on page 6. Relevance is measured in #20; semantic
  ranking is #21.
- Only the newest bulletin per series is indexed by default, and the first content
  runs cover the newest series first under the download budget.
- PDF tables (#8) are reachable through their page's passage, not as `table`
  documents; scanned PDFs have no text to index.
- Table values are not searchable; use `inspect_tuik_data`/`get_tuik_data` once the
  right table is found.
