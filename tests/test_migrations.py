"""The additive-migration helper, which took production down once.

Every column in _ADDITIVE_COLUMNS is checked, not just a sample. The failure
mode is that a new column looks fine on SQLite -- where the whole suite runs --
and is rejected only by Postgres, at startup, in production. A test that walks
the real table is the only thing standing between that and a deploy.
"""
import pytest

from app.database import _ADDITIVE_COLUMNS, postgres_ddl

ALL_COLUMNS = [
    (table, name, ddl)
    for table, columns in _ADDITIVE_COLUMNS.items()
    for name, ddl in columns
]


@pytest.mark.parametrize("table,name,ddl", ALL_COLUMNS, ids=[f"{t}.{n}" for t, n, _ in ALL_COLUMNS])
def test_no_column_definition_is_mangled_for_postgres(table, name, ddl):
    """A numeric default must survive the rewrite untouched.

    INTEGER DEFAULT 0 became DEFAULT FALSE and INTEGER DEFAULT 120 became
    DEFAULT TRUE20 -- the second is not valid SQL at all. The migration threw
    partway through and rolled back every column with it, leaving the deployed
    database missing fields the models select on every request.
    """
    out = postgres_ddl(ddl)
    if ddl.upper().startswith("BOOLEAN"):
        assert "DEFAULT 0" not in out and "DEFAULT 1" not in out
    else:
        assert out == ddl, f"{table}.{name} was rewritten to {out!r}"
        assert "TRUE" not in out.upper().replace("NOT NULL", "")


def test_booleans_are_still_translated():
    """The reason the rewrite exists. SQLite has no boolean type, so these are
    declared 0/1 and Postgres will not take them."""
    assert postgres_ddl("BOOLEAN DEFAULT 0 NOT NULL") == "BOOLEAN DEFAULT FALSE NOT NULL"
    assert postgres_ddl("BOOLEAN DEFAULT 1 NOT NULL") == "BOOLEAN DEFAULT TRUE NOT NULL"


@pytest.mark.parametrize("ddl", [
    "INTEGER DEFAULT 0 NOT NULL",
    "INTEGER DEFAULT 1 NOT NULL",
    "INTEGER DEFAULT 15 NOT NULL",
    "INTEGER DEFAULT 120 NOT NULL",
    "INTEGER DEFAULT 180 NOT NULL",
    "VARCHAR(8) DEFAULT '07:00' NOT NULL",
    "DATE",
    "TEXT",
])
def test_everything_that_is_not_a_boolean_passes_through(ddl):
    assert postgres_ddl(ddl) == ddl


def test_the_planning_columns_are_all_registered():
    """A column on the model with no migration entry works on a fresh database
    and is missing on every existing one -- which is every real deployment."""
    from app.models import User

    registered = {name for name, _ in _ADDITIVE_COLUMNS["users"]}
    planning = {
        "day_start", "day_end", "dinner_start", "dinner_end", "exercise_minutes",
        "exercise_when", "commute_minutes", "study_block_min", "study_block_max",
        "study_target_minutes", "break_minutes", "focus_period",
        "subject_priorities", "semester_start",
    }
    assert planning <= registered, f"missing: {planning - registered}"
    # And each of them is a real column on the model, not a stale name.
    columns = {c.name for c in User.__table__.columns}
    assert planning <= columns, f"not on the model: {planning - columns}"
