from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from faa_drs import DocType, InvalidQueryError, SortOrder, UnknownFieldWarning
from faa_drs._query import build_query


def test_plain_query_uses_get() -> None:
    method, path, kwargs = build_query(DocType.AC, offset=750).request_args()
    assert (method, path) == ("GET", "/AC")
    assert kwargs == {"params": {"offset": 750}}


def test_get_params_use_api_names() -> None:
    query = build_query("AC", modified_after=date(2025, 1, 2), sort="desc")
    _, _, kwargs = query.request_args()
    assert kwargs["params"] == {
        "offset": 0,
        "docLastModifiedDate": "2025-01-02T00:00:00.000Z",
        "docLastModifiedDateSortOrder": "DESC",
    }


def test_filtered_query_uses_post_with_api_names() -> None:
    query = build_query(
        "PMA",
        offset=10,
        modified_after="2021-12-21T14:04:31.062Z",
        sort=SortOrder.ASC,
        filters={"drs:status": ["Current", "Historical"], "drs:pmaNumber": "PQ04418CE"},
    )
    method, path, kwargs = query.request_args()
    assert (method, path) == ("POST", "/PMA/filtered")
    assert kwargs["json"] == {
        "offset": 10,
        "docLastModifiedDate": "2021-12-21T14:04:31.062Z",
        "sortOrder": "ASC",
        "documentFilters": {
            "drs:status": ["Current", "Historical"],
            "drs:pmaNumber": ["PQ04418CE"],
        },
    }


def test_doctype_is_url_quoted() -> None:
    assert build_query("ORDER_8900.1").request_args()[1] == "/ORDER_8900.1"
    assert build_query("A/B").request_args()[1] == "/A%2FB"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2025, 5, 9, 14, 38, 11, 964000, tzinfo=UTC), "2025-05-09T14:38:11.964Z"),
        (datetime(2025, 5, 9, 14, 38, 11), "2025-05-09T14:38:11.000Z"),  # noqa: DTZ001
        (
            datetime(2025, 5, 9, 10, 0, tzinfo=timezone(timedelta(hours=-4))),
            "2025-05-09T14:00:00.000Z",
        ),
        ("2025-05-09T14:38:11.964Z", "2025-05-09T14:38:11.964Z"),
        (date(2025, 5, 9), "2025-05-09T00:00:00.000Z"),
    ],
)
def test_modified_after_is_utc_millis(value: Any, expected: str) -> None:
    assert build_query("AC", modified_after=value).modified_after_param == expected


def test_date_filter_accepts_pairs_of_dates_or_strings() -> None:
    query = build_query("PMA", filters={"drs:pmaSupDate": (date(2020, 1, 1), "2025-07-31")})
    assert query.filters == {"drs:pmaSupDate": ["2020-01-01", "2025-07-31"]}


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("2020-01-01", "pair"),
        (date(2020, 1, 1), "pair"),
        (["2020-01-01"], "exactly 2"),
        (["2020-01-01", "not-a-date"], "not a YYYY-MM-DD"),
        (["2025-01-01", "2020-01-01"], "after end"),
    ],
)
def test_date_filter_errors(value: Any, message: str) -> None:
    with pytest.raises(InvalidQueryError, match=message):
        build_query("PMA", filters={"drs:pmaSupDate": value})


def test_unknown_filter_warns_with_close_match_and_is_kept() -> None:
    with pytest.warns(UnknownFieldWarning, match="Did you mean: drs:saibIssueDate") as record:
        query = build_query("SAIB", filters={"drs:saibIssuDate": "x", "drs:status": "Current"})
    assert len(record) == 1
    assert query.filters == {"drs:saibIssuDate": ["x"], "drs:status": ["Current"]}
    assert query.unknown_fields == ("drs:saibIssuDate",)


def test_known_fields_do_not_warn() -> None:
    assert build_query("SAIB", filters={"drs:status": "Current"}, keywords="x").unknown_fields == ()


def test_unknown_field_accepts_a_date_pair() -> None:
    with pytest.warns(UnknownFieldWarning):
        query = build_query("SAIB", filters={"drs:newDate": (date(2020, 1, 1), "2020-12-31")})
    assert query.filters == {"drs:newDate": ["2020-01-01", "2020-12-31"]}
    pair = (datetime(2020, 1, 1, tzinfo=UTC), date(2021, 1, 1))
    assert build_query("NEW_TYPE", filters={"d": pair}).filters == {
        "d": ["2020-01-01", "2021-01-01"]
    }


def test_generator_values_are_read_once() -> None:
    query = build_query("SAIB", filters={"drs:status": (s for s in ["Current", "Historical"])})
    assert query.filters == {"drs:status": ["Current", "Historical"]}


def test_unknown_doctype_skips_local_field_validation() -> None:
    query = build_query("NEW_TYPE", filters={"anything": "x"})
    assert query.filters == {"anything": ["x"]}


def test_values_are_stripped_deduped_and_empties_dropped() -> None:
    filters: Any = {"drs:status": [" Current ", "Current", "", None], "drs:saibMake": []}
    query = build_query("SAIB", filters=filters)
    assert query.filters == {"drs:status": ["Current"]}
    assert not build_query("SAIB", filters={"drs:status": "  "}).is_filtered


def test_keywords_become_keyword_filter() -> None:
    query = build_query("SAIB", keywords="corrosion")
    assert query.filters == {"Keyword": ["corrosion"]}
    merged = build_query("SAIB", filters={"Keyword": ["a"]}, keywords=["b", "a"])
    assert merged.filters == {"Keyword": ["a", "b"]}


def test_max_five_filters_including_keywords() -> None:
    filters = {
        "drs:status": "Current",
        "drs:saibMake": "Boeing",
        "drs:saibModel": "737",
        "drs:productType": "Aircraft",
        "drs:productSubType": "Large Airplane",
    }
    build_query("SAIB", filters=filters)
    with pytest.raises(InvalidQueryError, match="at most 5 filters"):
        build_query("SAIB", filters=filters, keywords="fuel")


def test_max_ten_values_per_filter() -> None:
    build_query("SAIB", keywords=[f"k{i}" for i in range(10)])
    with pytest.raises(InvalidQueryError, match="at most 10"):
        build_query("SAIB", keywords=[f"k{i}" for i in range(11)])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"offset": -1}, "offset"),
        ({"sort": "sideways"}, "sort"),
        ({"modified_after": "yesterday"}, "modified_after"),
        (
            {"modified_after": datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5)))},
            "out of range",
        ),
    ],
)
def test_invalid_arguments(kwargs: dict, message: str) -> None:
    with pytest.raises(InvalidQueryError, match=message):
        build_query("AC", **kwargs)


def test_non_string_filter_values_are_rejected() -> None:
    with pytest.raises(InvalidQueryError, match="must be strings"):
        build_query("SAIB", filters=_any({"drs:status": [1]}))
    with pytest.raises(InvalidQueryError, match="not a date field"):
        build_query("SAIB", filters={"drs:status": date(2020, 1, 1)})
    with pytest.raises(InvalidQueryError, match="string or a list"):
        build_query("SAIB", filters=_any({"drs:status": 5}))


def test_invalid_query_error_is_value_error() -> None:
    assert issubclass(InvalidQueryError, ValueError)


def _any(value: Any) -> Any:
    return value


def test_doctype_is_stripped_before_catalog_lookup() -> None:
    with pytest.warns(UnknownFieldWarning, match="catalog for document type 'SAIB'"):
        build_query("  SAIB ", filters={"drs:nope": "x"})
    assert build_query(" SAIB ").request_args()[1] == "/SAIB"


@pytest.mark.parametrize("doctype", ["", "   ", None])
def test_empty_doctype_is_rejected(doctype: Any) -> None:
    with pytest.raises(InvalidQueryError, match="doctype"):
        build_query(doctype)


@given(
    st.dictionaries(
        st.sampled_from(["drs:status", "drs:saibMake", "drs:saibModel"]),
        st.lists(st.one_of(st.none(), st.text(max_size=8)), max_size=12),
    )
)
def test_normalized_filters_are_clean(filters: dict[str, list[str | None]]) -> None:
    cleaned = {
        k: list(dict.fromkeys(v.strip() for v in vs if v and v.strip()))
        for k, vs in filters.items()
    }
    if any(len(values) > 10 for values in cleaned.values()):
        with pytest.raises(InvalidQueryError, match="at most 10"):
            build_query("SAIB", filters=_any(filters))
        return
    query = build_query("SAIB", filters=_any(filters))
    assert query.filters == {k: v for k, v in cleaned.items() if v}


_LOW, _HIGH = datetime(2, 1, 1), datetime(9998, 12, 31)  # noqa: DTZ001
# Fixed offsets, not st.timezones(): that needs the IANA database, which Windows lacks.
_OFFSETS = st.builds(
    timezone, st.timedeltas(min_value=timedelta(hours=-23), max_value=timedelta(hours=23))
)


@given(
    st.one_of(
        st.datetimes(_LOW, _HIGH),
        st.datetimes(_LOW, _HIGH, timezones=_OFFSETS),
    )
)
def test_modified_after_param_round_trips_to_the_millisecond(value: datetime) -> None:
    param = build_query("AC", modified_after=value).modified_after_param
    assert param is not None
    assert param.endswith("Z")
    parsed = datetime.fromisoformat(param)
    expected = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    assert parsed == expected.replace(microsecond=expected.microsecond // 1000 * 1000)
