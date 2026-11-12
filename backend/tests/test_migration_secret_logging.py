"""Prevent the RLS signing secret from appearing in migration SQLAlchemy errors."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def test_alembic_migration_engine_hides_bound_parameters_on_error():
    env_path = Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    assert "hide_parameters=True" in env_path.read_text(encoding="utf-8")

    sentinel = "MIA_RLS_CONTEXT_SECRET_TEST_SENTINEL"
    engine = create_engine("sqlite://", hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE secret_probe (value TEXT CHECK (value = 'safe'))"
            ))

        with pytest.raises(IntegrityError) as error:
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO secret_probe (value) VALUES (:secret)"),
                    {"secret": sentinel},
                )

        assert sentinel not in str(error.value)
        assert "SQL parameters hidden" in str(error.value)
    finally:
        engine.dispose()
