## Goal

Choose an integration that measurably reduces the last mile from a validated artifact to daily office work.

## Context

Writing a local PPTX is not the same as editing a native Google Slides presentation or live Microsoft 365 file. Native APIs, upload/import, permissions and editability vary. Do not assume Microsoft Graph exposes a general PowerPoint authoring API.

## Proposal

Compare three paths: portable files, host-composed use of existing Office/Drive connectors, and a dedicated optional adapter. Test PPTX/XLSX handoff into Google Drive/Slides/Sheets and OneDrive/Excel with synthetic or public evidence only; inspect chart/text/citation fidelity after import. Verify provider-specific supported APIs and least-privilege delegated scopes. Select one demand-backed pilot; defer the second provider if it adds little measured value.

## Acceptance criteria

- [ ] Publish a capability/permission/round-trip matrix and source links to current provider APIs, including unsupported operations.
- [ ] One selected provider has a reproducible create/import → read-back → update-existing-owned-artifact prototype, or a documented no-go with evidence.
- [ ] Citations, numbers, Turkish text, chart editability and artifact/remote IDs survive the tested path; conversion losses are explicit.
- [ ] Idempotent updates target the created file by ID and preserve user edits outside managed sections; failures do not duplicate files.
- [ ] Credentials remain outside tool arguments/logs, target ownership is checked and writes/sharing are explicit operations; no auto-email/public sharing.
- [ ] Decide whether host composition is sufficient before adding a second credentials/connector stack to the core server.

## TÜİK sources involved

[Google Slides API](https://developers.google.com/workspace/slides/api/guides/overview), [Drive uploads](https://developers.google.com/workspace/drive/api/guides/manage-uploads), [Microsoft Graph](https://learn.microsoft.com/en-us/graph/api/overview?view=graph-rest-1.0).

## Planning

- **Phase:** 4 — institutional integrations
- **Priority:** medium; **relative size:** M (not a delivery-date estimate).
- **Release scope:** expansion; does not gate the first institutional-output slice.

## Dependencies

- **Blocked by:** #93, #92
- **Related:** #102
- **Epic:** #84
- **Roadmap:** #25
