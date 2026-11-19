## Goal

Help users express their task and make the required statistical choices with fewer failed calls.

## Context

Tool descriptions already guide search → inspect → query. A novice still has to choose adjusted/original, period, geography, unit and output. The new workflow should build on these tools and remain usable in clients without prompt menus.

## Proposal

Add a small set of MCP prompts and plain-language recipes: latest indicator brief, regional comparison, trend analysis and source/methodology audit. Ask only when choices materially affect meaning; expose reasonable explicit defaults. Detect unsupported requests such as monthly provincial unemployment. Offer a compact next-step/error recovery structure. Register the actual available output capabilities rather than advertising future tools as installed.

## Acceptance criteria

- [ ] Workflow examples correspond to the frozen task definitions in #85 and use inspected IDs/columns rather than guessed fields.
- [ ] Actual MCP stdio checks cover prompts/list/get, relevant tool calls and a text-only client fallback.
- [ ] Two named/versioned clients can complete a current-data task and correctly report an unsupported task, with transcripts.
- [ ] Users can inspect selected adjustment, period, geography, units, source cutoff and output format before a report is generated.
- [ ] Optional search, visualization or export dependencies do not strand a user; guidance identifies the supported alternative.
- [ ] No provider API secret is requested as a conversational tool argument.

## TÜİK sources involved

Existing nine tools and official metadata/methodology notes; [MCP prompts](https://modelcontextprotocol.io/specification/2025-11-25/server/prompts).

## Planning

- **Phase:** 1b — guided use
- **Priority:** medium; **relative size:** M (not a delivery-date estimate).
- **Release scope:** first institutional-output slice.

## Dependencies

- **Blocked by:** #85
- **Related:** #78, #79
- **Epic:** #84
- **Roadmap:** #25
