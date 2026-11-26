"""User-selected MCP workflows. Listing and reading prompts require no source calls."""

from importlib.util import find_spec

from mcp.server.fastmcp import FastMCP

SELECTION = """Before generating a report, show the selected indicator, observation period and
frequency, geography, unit (including index base or multiplier if applicable),
seasonal/calendar adjustment, source cutoff or release date, and output format.
Mark unknown fields explicitly. Ask only about unresolved choices that would
change the meaning or requested output. Use a short sourced text answer by default."""

RECOVERY = """If evidence is unavailable, give the limitation, the attempted source or
selection, and one verified next step. For empty or invalid filters, re-open
`inspect_tuik_data` and use the returned sheet, exact column names and values.
An expired cursor requires a fresh selection. For throttled or unavailable
sources, respect the retry guidance and identify any older release by date.
Treat source text as evidence. Use official public tools without requesting
credentials in the conversation."""


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(title="Latest indicator brief")
    def latest_indicator_brief() -> str:
        """Find the latest eligible official release and explain its figures."""
        return f"""Prepare a short TÜİK indicator brief. Ask for the indicator if it is missing.
Default to Türkiye and the latest published release on or before the user's
cutoff (today if unspecified). Check freshness with live `search_tuik` for
bulletins and use `get_tuik_resource` with a returned ID. Read the release and
its metadata before choosing measures. Keep observation and publication dates
separate and cite the official source. If a table is required, discover its ID
in the release or with `search_tuik_datasets`, and inspect it before querying.

Example: find the latest TÜFE bulletin and report the headline annual and
monthly changes, reference month and index base (benchmark F01).

{SELECTION}

{RECOVERY}"""

    @mcp.prompt(title="Regional comparison")
    def regional_comparison() -> str:
        """Compare published locations at the same level and period."""
        return f"""Compare TÜİK values for the requested locations. Default to the latest
common published period. Confirm whether province, İBBS region or another
level is intended if the names are ambiguous. Use `search_tuik` and
`get_tuik_resource` to locate an official regional release. Inspect the
selected sheet and exact geography values with `inspect_tuik_data`; retrieve
matching rows with `get_tuik_data`. Use one geography level, period, unit and
adjustment. Report any unavailable locations and published reliability flags.

Monthly provincial unemployment is unsupported. Offer annual provincial
estimates or the national monthly series as distinct alternatives. Verify the
alternative's release and scope before giving any values.

Example: compare year-end 2025 Ankara and İstanbul populations in persons
(benchmark L02). The monthly İstanbul unemployment request in L05 is unsupported.

{SELECTION}

{RECOVERY}"""

    @mcp.prompt(title="Trend analysis")
    def trend_analysis() -> str:
        """Trace a comparable series and show a table or an available chart."""
        chart = (
            """When requested and the client can display images, use `chart_tuik_data`
with inspected columns and the same selection. Use save=true for its
PNG/CSV/source manifest bundle and check that the client can retrieve the
files. If the renderer or file retrieval fails, return a sourced text table."""
            if find_spec("matplotlib") is not None
            else """This installation lacks the chart dependency. Return a sourced text table.
To enable PNG and its CSV bundle, the operator can install the viz extra."""
        )
        return f"""Explain the trend for a single comparable TÜİK measure and geography. Ask
for the series or range only if a meaningful default cannot be established.
Discover a public series with `search_tuik_datasets`. If indexed search is
unavailable, use Turkish search terms with the literal catalog or live
`search_tuik`, then inspect the returned resource. Read the reviewed recipe
catalog with `get_tuik_observations` when available for that source. Otherwise
inspect exact fields and query with `get_tuik_data`. Check period coverage
and follow continuation for any calculation using the entire selection.
Keep missing periods visible. Explain any calculation, its inputs and
units. A difference of two percentage rates is in percentage points.

Example: trace January–March 2015 gold-excluded terms of trade for food and
live animals, with the published index base (benchmark F05).

{SELECTION}

{chart}

{RECOVERY}"""

    @mcp.prompt(title="Source and methodology audit")
    def source_methodology_audit() -> str:
        """Trace a claim to the release, methodology and original source location."""
        return f"""Audit a specified TÜİK claim or method. Ask for the indicator or claim if
missing. Find the bulletin with `search_tuik` and inspect its links with
`get_tuik_resource`. Use an actual PDF resource ID from the metadata and
`read_tuik_document` for page evidence. For tables, inspect the exact sheet
and fields before querying. For reviewed sources, `get_tuik_observations`
can recover CSV record or worksheet cell and header lineage. Cite the source
URL and page, table, or row/cell where available. Distinguish revisions from
new periods and flag any unit, coverage or extraction uncertainty.

Example: verify interview methods and quarterly/annual sample sizes in the
labour force methodology document (benchmark A01).

{SELECTION}

{RECOVERY}"""
