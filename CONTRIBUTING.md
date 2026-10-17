# Contributing

This project follows **Git Flow**. Nobody commits directly to `master` or `develop`;
every change arrives through a pull request that passes CI and gets one review.

## Branches

| Branch | Purpose | Created from | Merges into |
| --- | --- | --- | --- |
| `master` | Released code only. Every commit is a tagged version. | — | — |
| `develop` | Integration branch; the next release in progress. | `master` | — |
| `feature/<issue>-<slug>` | New tools or capabilities | `develop` | `develop` |
| `fix/<issue>-<slug>` | Non-urgent bug fixes | `develop` | `develop` |
| `docs/<slug>`, `chore/<slug>`, `refactor/<slug>` | Docs, tooling, cleanup | `develop` | `develop` |
| `release/<x.y.z>` | Release stabilization: version bump, changelog, final checks | `develop` | `master` **and** `develop` |
| `hotfix/<x.y.z>` | Urgent fix for a released version | `master` | `master` **and** `develop` |

Examples: `feature/12-xlsx-ingestion`, `fix/18-csv-semicolon-delimiter`,
`release/0.2.0`, `hotfix/0.1.1`.

## Day-to-day workflow

1. Open (or pick) an issue and assign yourself.
2. Branch from an up-to-date `develop`:

   ```bash
   git switch develop && git pull
   git switch -c feature/12-xlsx-ingestion
   ```

3. Commit in small steps using [Conventional Commits](https://www.conventionalcommits.org/):
   `feat: ...`, `fix: ...`, `docs: ...`, `test: ...`, `refactor: ...`, `chore: ...`.
4. Before pushing, run the same checks CI runs:

   ```bash
   .venv/bin/ruff check src tests scripts
   .venv/bin/ruff format --check src tests scripts
   .venv/bin/pytest
   ```

   If you touched a TÜİK adapter, also run `.venv/bin/pytest -m live`.
5. Push and open a PR into `develop`. Fill in the template, link the issue
   (`Closes #12`), and add an entry under `[Unreleased]` in `CHANGELOG.md`.
6. Merge after CI is green and one teammate approves. Use **squash merge** so each
   feature lands on `develop` as one Conventional Commit. Delete the branch afterwards.

## Releasing

1. `git switch -c release/0.2.0 develop`
2. Bump `version` in `pyproject.toml`, move `[Unreleased]` entries in
   `CHANGELOG.md` under `[0.2.0] - YYYY-MM-DD`, and refresh `docs/VERIFICATION.md`
   with a live run if needed. Only fixes go on a release branch, no new features.
3. Open a PR `release/0.2.0 → master`. Merge with a **merge commit** (not squash),
   so `master` and `develop` keep a shared history.
4. Tag the merge commit on `master` and push the tag:

   ```bash
   git switch master && git pull
   git tag -a v0.2.0 -m "v0.2.0"
   git push origin v0.2.0
   ```

5. Merge `master` back into `develop` (PR `master → develop`) so the version bump
   and release fixes are not lost.

Hotfixes follow the same steps, branching from `master` and bumping the patch version.

## Versioning

[Semantic Versioning](https://semver.org/). While the project is `0.x`, a minor
bump (`0.2.0`) may change tool signatures; patch bumps (`0.1.1`) only fix bugs.
Tool names and parameters are the public API, so call out any change to them in
the changelog.

## Repository settings (admin)

Configure branch protection on GitHub for both `master` and `develop`:

- Require a pull request before merging, with at least 1 approval
- Require status checks: the CI `Lint and test` jobs and `Docker build`
- Require branches to be up to date before merging
- Block force pushes and deletions
