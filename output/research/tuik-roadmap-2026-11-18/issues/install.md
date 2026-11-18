## Goal

Reduce the installation and diagnosis burden for people outside the development team.

## Context

The README currently starts from a source checkout and Python environment. Other servers promote uvx/npx or hosted URLs. Ease of setup is already a competitive baseline.

## Proposal

Verify ownership/name collisions before selecting a distribution name; build and test wheel/sdist with declared optional extras. Add a read-only doctor command that reports Python/runtime version, writable artifact storage, installed extras, optional index/model readiness and endpoint availability without exposing secrets. Document tested stdio setup for two selected clients and an explicit Windows path. Keep search/model services optional and distinguish startup errors from upstream outages.

## Acceptance criteria

- [ ] Clean machines/environments can install the built artifact without the repository checkout and discover the current tool set.
- [ ] CI exercises core-only, visualization/report extras and a no-index path; installation never silently downloads embedding models on an ordinary tool call.
- [ ] Test setup on Windows and macOS/Linux (actual platform or CI evidence) and record client versions.
- [ ] Doctor prints actionable next steps, preserves existing config, performs no credential writes and redacts secret values/headers.
- [ ] Public package publication is a separate release action after artifact checks and name ownership are verified; no claimed install command points to another project.
- [ ] Docs include artifact retrieval and optional dependency behavior, with a short novice setup trial.

## TÜİK sources involved

Current package and distribution metadata; existing official TÜİK endpoints for opt-in connectivity checks.

## Planning

- **Phase:** 1b — guided use
- **Priority:** high; **relative size:** M (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** none; can start independently.
- **Related:** #88, #85
- **Epic:** #84
- **Roadmap:** #25
