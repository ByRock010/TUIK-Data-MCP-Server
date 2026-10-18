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

## Issue closing on develop

The `Close issues after develop merge` workflow closes completed issues after
an approved PR is merged into `develop`. Keep the repository default branch
`master`; branch protection and rulesets do not change GitHub's built-in closing
keyword target. This workflow supplies the additional develop behavior.

Use a standalone closing directive in the **PR description** only when all of
that issue's acceptance criteria are complete:

```text
Closes #12
```

`Fixes #12` and `Resolves #12` are also accepted (including the other standard
English keyword forms, case-insensitively). For partial work use `Refs #12`.
For example, #28 must remain a reference until its GitHub-hosted acceptance runs
are complete. Each issue still has its own branch and PR into `develop`.

The parser accepts multiple references separated by commas/semicolons or on
separate directive lines. Same-repository qualified references and GitHub issue
URLs are supported; references to other repositories are ignored. Ordinary
mentions, PR titles, branch names and commit messages do not trigger this
workflow. Examples inside fenced/indented code, inline code, HTML code blocks,
HTML comments and blockquotes are ignored. Put the actual directive outside the
code block, as the PR template does.

On a non-forced push to `develop`, GitHub's associated-PR API is checked against
the exact pushed SHA. The canonical PR must be merged, target this repository's
`develop` branch and have that exact `merge_commit_sha`. Only then does the
workflow close its explicitly referenced open issues as `completed`. It does not
close PRs, already-closed issues or missing issue numbers. Retries are idempotent.
A direct commit without a matching merged PR does not close anything. This also
supports PRs merged from forks: the workflow runs from the trusted develop push,
with the base repository's token, rather than executing an unmerged PR head.

No PAT or additional secret is needed. The job requests `contents: read`,
`pull-requests: read` and `issues: write`; it neither changes the default branch
nor weakens merge/review rules. Closing uses the Issues API and creates no bot
comments. The Actions log identifies the PR, source SHA and each issue it closes.
If closing fails, inspect the workflow run and correct the directive/permission
before retrying. A missing issue logs a warning and is skipped.

Activation: merge the automation PR into `develop` through the normal CI/review
process. Its `push` trigger works from `develop`; it does not need a release to
`master` or a default-branch workflow_dispatch bootstrap. Earlier PR merges are
not backfilled. GitHub's native `Closes` behavior on `master` continues to work.

Offline automation checks (also run in CI):

```bash
node --test .github/scripts/close-develop-issues.test.cjs
```

See [GitHub's native issue-closing behavior](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)
and [push event workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#push).

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
