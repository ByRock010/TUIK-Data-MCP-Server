"""Range operands need the same ambiguity guarantees as source values (#52 review)."""

import pytest

from tuik_mcp.models.query import TableQuery
from tuik_mcp.parsers.models import ParsedTable
from tuik_mcp.parsers.query import QueryError, QueryLimits, evaluate


def ranged(values, bound, operand, **options):
    table = ParsedTable(
        columns=["nufus"],
        rows=[{"nufus": v} for v in values],
        total_rows=len(values),
        extraction="csv",
    )
    query = TableQuery.model_validate(
        {
            "predicates": [
                {
                    "op": "range",
                    "column": "nufus",
                    "value_type": "number",
                    bound: operand,
                    **options,
                }
            ]
        }
    )
    _, rows = evaluate(table, query, {}, QueryLimits())
    return [row["nufus"] for row in rows]


@pytest.mark.parametrize("bound", ["minimum", "maximum"])
@pytest.mark.parametrize("operand", ["1.234", "1,234"])
def test_integer_column_does_not_disambiguate_three_digit_fraction_bounds(bound, operand):
    with pytest.raises(QueryError) as error:
        ranged(["47938", "1500", "999", "1234"], bound, operand)
    assert error.value.code == "invalid_query"
    message = str(error.value)
    assert f"Range {bound}" in message and operand in message and "decimal_separator" in message


@pytest.mark.parametrize("bound,expected", [("minimum", ["2", "1234"]), ("maximum", ["0", "1"])])
@pytest.mark.parametrize("separator", [".", ","])
def test_explicit_separator_resolves_ambiguous_bounds_without_treating_it_as_grouping(
    bound,
    expected,
    separator,
):
    operand = "1" + separator + "234"
    assert ranged(["0", "1", "2", "1234"], bound, operand, decimal_separator=separator) == expected


@pytest.mark.parametrize("separator", [".", ","])
def test_unambiguous_source_decimals_establish_the_matching_bound_separator(separator):
    source = ["0" + separator + "5", "2" + separator + "25"]
    assert ranged(source, "minimum", "1" + separator + "234") == source[1:]


def test_plain_integer_bound_is_never_treated_as_a_decimal_or_grouped_number():
    assert ranged(["47938", "1500", "999", "1234"], "minimum", "1234") == [
        "47938",
        "1500",
        "1234",
    ]


@pytest.mark.parametrize("bound", ["minimum", "maximum"])
def test_wrong_bound_separator_is_reported_as_caller_input_not_column_data(bound):
    with pytest.raises(QueryError) as error:
        ranged(["98,46", "101,66"], bound, "100.5")
    message = str(error.value)
    assert f"Range {bound}" in message and "100.5" in message
    assert "decimal_separator" in message and "','" in message and "Column" not in message


@pytest.mark.parametrize("bound", ["minimum", "maximum"])
def test_missing_bound_is_identified_by_operand_name(bound):
    with pytest.raises(QueryError) as error:
        ranged(["0", "1"], bound, "-")
    assert f"Range {bound}" in str(error.value)
