# Report artifacts (#88)

A report artifact is an immutable, server-owned bundle that a user can download:
generated files, the exact source files they came from, and a versioned
`manifest.json` that pins every input. It is the foundation for the planned
Excel/PowerPoint/PDF exports (#92–#94). The first producer is
`chart_tuik_data(..., save=true)`.

## What a bundle holds

`chart_tuik_data` with `save=true` writes:

| File | Role | Contents |
| --- | --- | --- |
| `chart.png` | output | The chart, with the source page and retrieval date drawn on it |
| `chart-data.csv` | data | The plotted values, one row per x label, one column per line; empty cells are gaps |
| `README.txt` | notes | Title, unit, the visible citation and every warning |
| `source.<csv\|xls\|xlsx>` | source | The unchanged official file the chart was drawn from |
| `manifest.json` | — | The full provenance record, readable like any other file |

The manifest (`schema_version` 1) lists:
- **The generator:** `tuik-mcp` and its version.
- **Each input:** resource ID, official page, the verified download URL at retrieval time, format, `retrieved_at`, publication date, SHA-256 and size, and the exact selection (x, y, sheet, filters, query, `last`, …). It also records whether the source was retained, and as which file.
- **Mixed inputs:** `retrieval_times_differ`, with a warning, whenever inputs were downloaded at different times.
- **Processing:** the transformations applied and every warning.
- **Each file:** name, role, MIME type, size, SHA-256 and resource URI.

## Retrieving files

The tool result stays small. It holds the summary (`artifact_id`, `expires_at`, `manifest_uri`, the file list) and one MCP `resource_link` per file. The bytes are never placed in the text.

- **MCP clients** read `tuik-artifact://<artifact_id>/<name>` with `resources/read`.
  - Binary files arrive as blobs with their own MIME type.
  - UTF-8 text (CSV, notes, manifest) arrives as text; a source in another encoding stays a byte-exact blob.
  - The template is listed by `resources/templates/list`.
  - Every read re-checks the file's size and SHA-256 against the manifest.
- **Clients that do not show resource links:** on the computer running the server, the user can copy a whole bundle with
  ```bash
  tuik-mcp-artifacts list
  tuik-mcp-artifacts show <artifact_id>
  tuik-mcp-artifacts export <artifact_id> --to ~/Downloads
  ```
  `export` creates `<directory>/<artifact_id>/`, verifies every hash and never overwrites existing files.

A server-local path is never reported as a download. A future hosted server (#102/#103) must deliver files over its own transport.

## Storage, budgets and lifetime

| Setting | Default | Meaning |
| --- | --- | --- |
| `TUIK_ARTIFACT_DIR` | `~/.cache/tuik-mcp/artifacts` | Server-owned root, separate from the parse/query caches |
| `TUIK_ARTIFACT_TTL_SECONDS` | 86400 | Lifetime of a bundle (1 minute – 30 days) |
| `TUIK_ARTIFACT_MAX_TOTAL_BYTES` | 268435456 | All bundles together; the oldest are evicted to make room |
| `TUIK_ARTIFACT_MAX_FILE_BYTES` | 16777216 | One file; a larger source is not retained, with a note |
| `TUIK_ARTIFACT_MAX_FILES` | 16 | Files per bundle, including the manifest |
| `TUIK_ARTIFACT_MAX_JOBS` | 2 | Bundles written at the same time |
| `TUIK_ARTIFACT_RETAIN_SOURCES` | true | Keep the exact source file in each bundle |

- **Immutable.** A bundle is written in a private staging directory and published with one rename. Its files are never modified.
- **Persistent.** Bundles survive restarts: a new server process serves an older bundle until it expires.
- **No silent refresh.** Saving again downloads current data into a new bundle with a new ID; an old ID is never refreshed or substituted.
- **Actionable failures:**
  - An expired bundle answers `expired`, with its expiry time.
  - An evicted or unknown one answers `not_found`.
  - A file that no longer matches its hash answers `corrupted`.
  - Each message says to save again for a new artifact.
- **Retention policy.** Retained sources are the unchanged official files, kept so every figure can be re-checked; their reuse follows TÜİK's terms of use. A source above the file limit, or any source when retention is disabled, is recorded with its hash and a `retention_note` instead.

## Safety

- **No caller-chosen paths.** IDs are `art_` plus 32 random hex digits, and file names come from the server. Both are validated again on every read (letters, digits, `-`, `_`, one extension, no `..`).
- **No symlinks.** Files are created exclusively without following symlinks, inside a private (`0700`) root that must not itself be a symlink. A symlinked bundle or file, or a path that resolves outside the root, is refused.
- **Bounded storage.** Expired, unreadable and abandoned staging directories are swept before each write. Staging directories are only swept after an hour, so a concurrent job is never interrupted.
- **One quota for every writer.** `manifest.json` counts against the file and total limits like any other file; a selection too large for the manifest is refused. Saves running at the same time reserve their bytes under one lock, and a save that would overshoot waits for bundles in progress to publish, so stored and in-progress bytes never exceed the total. A cancelled save keeps its job slot until its worker thread exits.
- **One writer.** The store expects a single server process per directory. Several processes sharing one directory may evict each other's bundles when the total budget is reached.

## Limits of this slice

- `chart_tuik_data` is the only producer; Office/PDF renderers (#92–#94) will reuse the same manifest and delivery.
- The CSV carries no embedded citation; it travels with `README.txt` and the manifest. The PNG shows the citation itself.
- Bundles hold one retrieval of each source. Combining several retrievals into one report is supported by the manifest (`retrieval_times_differ`) but has no producer yet.

## Verify

```bash
.venv/bin/pytest tests/test_artifacts.py -q
```
