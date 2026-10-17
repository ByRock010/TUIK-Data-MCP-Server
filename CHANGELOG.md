# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Git Flow process: `develop` branch, CI workflow, PR and issue templates,
  `CONTRIBUTING.md` and this changelog.

### Changed

- README client configuration uses a generic path instead of a developer's machine path.

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
