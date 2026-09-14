import asyncio
import os
import sys
from pathlib import Path

import pytest

os.environ["ENVIRONMENT"] = "test"

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    import app.models.database as db_mod
    import main as main_mod
    from app.models.database import Base
    from app.shopify.config import settings

    db_path = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    engine = create_async_engine(
        db_url,
        connect_args={"check_same_thread": False},
    )

    session_local = sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    monkeypatch.setattr(settings, "database_url", db_url)

    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", session_local)

    application = main_mod.create_app()

    with TestClient(application) as test_client:
        yield test_client

    asyncio.run(engine.dispose())


@pytest.fixture()
def db_session(tmp_path):
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.database import Base

    db_path = tmp_path / "service-test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    engine = create_async_engine(
        db_url,
        connect_args={"check_same_thread": False},
    )

    session_local = sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(setup())

    async def run():
        async with session_local() as session:
            yield session

    session = asyncio.run(_get_session(session_local))

    yield session

    asyncio.run(session.close())
    asyncio.run(engine.dispose())


async def _get_session(session_local):
    session = session_local()
    return session
