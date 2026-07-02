"""Shared pytest fixtures for the XAR suite."""
import pytest


@pytest.fixture(scope="session")
def seeded_db():
    """Ensure schema + companies + seed graph exist (idempotent). Session-scoped so the
    one-time seed is shared; requesting tests get a populated local Postgres."""
    from xar.ingestion import seed_companies
    from xar.kg import store
    from xar.storage import db
    db.init_schema()
    seed_companies()
    store.bootstrap_seed()
    return db
