"""Regression test: a Meta Page id must never bind to the int32 primary key.

Entering a real Facebook Page id (a 16-digit number) in a publishing request used
to crash with asyncpg "value out of int32 range" (HTTP 500). _account_match now
only compares an out-of-int32 id against the string external_account_id column.
"""
from app.routers.social import _account_match


def _sql(clause):
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


def test_small_internal_id_matches_pk_or_external():
    sql = _sql(_account_match(2))
    assert " OR " in sql  # both the integer PK and the external-id string are checked
    assert "2" in sql


def test_oversized_page_id_is_string_match_only():
    # 1722891458806743 overflows int32; it must not reach the integer PK comparison.
    sql = _sql(_account_match(1722891458806743))
    assert " OR " not in sql  # single condition -> only external_account_id
    assert "1722891458806743" in sql


def test_non_numeric_value_is_string_match_only():
    sql = _sql(_account_match("not-a-number"))
    assert " OR " not in sql
    assert "not-a-number" in sql
