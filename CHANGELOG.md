# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Automatically close explicitly completed issues when their PR is merged into
  `develop`, with offline tests and documented PR-body closing directives.
- Weekly, optional live contract checks with JSON/JUnit/log artifacts, deduplicated
  failure issue reporting, a no-traffic reporting exercise and a triage guide (#28).
- Bulletin metadata exposes related dataflows with catalog export formats and
  category paths. `get_tuik_data` returns `related_datasets` choices for these
  bulletins; callers select a reusable `flow:` ID to retrieve CSV rows (#26).
- Optional local OpenSearch 3.3.2 Compose stack, Dashboards profile, search client
  extra and validated environment settings (#12). Existing tools stay live-only.
- A 12-file, 2.64 MB official XLS/XLSX/PDF fixture corpus with provenance, SHA-256,
  reviewed golden source observations and a paced refresh script (#4).
- Shared parser models and registry, with CSV parsing offloaded from the MCP event
  loop and parsing library decisions recorded in ADR 0001 (#3).

- Git Flow process: `develop` branch, CI workflow, PR and issue templates,
  `CONTRIBUTING.md` and this changelog.

### Changed

- Weekly live contract checks run the full MCP smoke workflow once, preserving
  the separate JSON report without duplicating upstream requests (#28).

- README client configuration uses a generic path instead of a developer's machine path.

### Fixed

- Bulletin dataflows retain category paths at every depth. Catalog outages preserve
  bulletin metadata without caching unconfirmed availability, and unavailable
  related datasets no longer prevent existing file downloads (#26).
- Empty or whitespace-only `TUIK_OPENSEARCH_URL` values disable the optional backend
  instead of preventing the MCP server from starting (#12).
- Production download verification recognizes XLSX archives served with XLS media
  types; interrupted fixture refreshes retain unprocessed resume entries (#4).
- Parser failures are distinct from invalid caller input; the CSV adapter keeps
  its existing source-error codes (#3).

- TÜİK's download throttle page is no longer reported as `unsupported_download`.
  Table/document downloads run one at a time at least 6 seconds apart
  (`TUIK_DOWNLOAD_INTERVAL_SECONDS`), the page's countdown is honored with a retry,
  and persistent throttling returns `download_throttled` (#2).

## [0.1.0] - 2026-10-17

### Added

- MCP stdio server with `search_tuik`, `get_tuik_resource`, `get_tuik_data`,
  `list_tuik_themes` and `search_tuik_classifications`.
- `search_tuik_datasets` for the public export catalog, with theme and
  downloadable-only filters and reusable `flow:` IDs.
- Bounded, rate-limited HTTP client with host allowlist and in-memory TTL cache.
- Offline test suite, opt-in live tests and `scripts/live_smoke.py`.
- Dockerfile.

[Unreleased]: https://github.com/ByRock010/TUIK-Data-MCP-Server/compare/v0.1.0...develop
[0.1.0]: https://github.com/ByRock010/TUIK-Data-MCP-Server/releases/tag/v0.1.0
