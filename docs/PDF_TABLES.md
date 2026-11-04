# Best-effort PDF tables (#8)

Bulletin tables already come from XLS files (#5) and dataflow CSV exports (#26), so
PDF tables matter for publications (*Yayın*) and reports (*Rapor*), which are PDF only.
`read_tuik_document(..., tables=true)` returns the requested page's tables next to
its text. Extraction is best effort and always says so: values are strings as
printed, nothing is typed, and each table carries a warning to verify it against
the page text.

## Method

`tuik_mcp.parsers.pdf_tables` runs on the same deduplicated pdfplumber page as the
text (`dedupe_chars()` also fixes doubled bold glyphs in cells):

1. **Ruled grids only.** pdfplumber's default line strategy finds tables drawn
   with ruling lines or cell rectangles. The text-alignment strategy was rejected:
   it reported a "table" on 16 of 17 publication pages and 9 of 10 methodology
   pages, almost all of them ordinary prose.
2. **Discard frames.** Grids with fewer than two columns or no text are
   decorative boxes (the publication cover has one).
3. **Join zebra fragments.** Tables whose rows alternate between shaded and plain
   are found as one fragment per shaded band. Vertically stacked fragments with
   the same column boundaries (2 pt tolerance), separated by at most 1.6 row
   heights, are one table. Plain rows between fragments, plus one trailing row
   that fills at least two columns, are rebuilt from word positions and counted
   in a warning.
4. **Title and header.** A first row with one cell spanning all columns, followed
   by a header, is the title; otherwise the title is the text line within 30 pt
   above the table, if any. A header row has distinct, non-empty, non-numeric
   cells. Without one, `layout` is `raw`, columns are `column_1`... and a warning
   says the table may continue from an earlier page.
5. **Cells.** Line breaks become spaces, except inside ASCII codes such as
   `INT_FAAL_SAGLIK_TIBKONS_REC|ETE`, which a narrow column wraps mid-token; those
   are joined. Line-end hyphenation is removed as in page text. Cells covered by
   a merged cell are empty strings; values are not copied into them.

Budgets: at most 20 tables and 10,000 cells per page (`total_rows` still counts
every row, and a warning reports truncation). Pages with more than 20,000 ruling
segments, which are charts or maps, skip detection with a page warning. Text and
tables share one parse and the one-hour snapshot cache; detection took 0.1–0.8 s
per page on the fixtures.

## Measurement (2026-11-02)

The two recorded official PDFs in `tests/fixtures/files` (provenance in
`manifest.json`): every page was extracted and compared with its page text.

| Document | Pages | Tables in the source | Found | Usable as returned |
| --- | --- | --- | --- | --- |
| *Hanehalkı Bilişim Teknolojileri Kullanım Araştırması Mikro Veri Seti, 2026* (publication) | 17 | 8 (one data dictionary over pages 5–12) | 8 | 8: rows complete; page 5 titled with header, pages 6–12 `raw` |
| *Metodolojik açıklama dokümanı* (labour bulletin methodology) | 10 | 1 (Ek 1, page 9) | 1 | 1: header and all 12 rows |

"Usable" means every source row is present with its cells in the right column.
For the publication, every variable code in each page's text appears in that
page's table (a test enforces this; the table even rejoins codes that the page
text splits across lines). Pages 6–12 continue page 5's dictionary without
repeating its header, so they are `raw`: complete, but with positional column
names. No false positives occurred: the cover frame was discarded, and Ek 2
(page 10, a flowchart drawn without a grid) is correctly not a table.

Zebra joining matters: without it, Ek 1 came back as six fragments holding the
header and only 6 of its 12 months. With it, the table has all 12, of
which 6 were rebuilt from word positions.

## Known limits

- Only ruled tables are found; borderless tables and charts return none.
- Continuation pages do not inherit the header from an earlier page. Read the
  first page of the table for its column names.
- Words wrapped inside a narrow header cell keep their break as a space
  (`Kayı t uzun luğu` for *Kayıt uzunluğu*).
- Rebuilt unruled rows are one row per text line; a wrapped cell in such a row
  would become two rows.
- Two documents are a small sample. Measuring more publications needs live TÜİK
  access, and tables found later should extend these fixtures. Scanned pages need
  OCR, which is out of scope.

## Verify

```bash
.venv/bin/pytest tests/test_pdf_tables.py -q
```
