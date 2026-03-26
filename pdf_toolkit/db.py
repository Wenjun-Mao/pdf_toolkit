from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base
from .settings import Settings, get_settings


@lru_cache(maxsize=4)
def _engine_for_url(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, future=True, connect_args=connect_args)


def get_engine(settings: Settings | None = None):
    active_settings = settings or get_settings()
    return _engine_for_url(active_settings.database_url)


@lru_cache(maxsize=4)
def _session_factory_for_url(database_url: str):
    engine = _engine_for_url(database_url)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_session_factory(settings: Settings | None = None):
    active_settings = settings or get_settings()
    return _session_factory_for_url(active_settings.database_url)


def init_db(settings: Settings | None = None) -> None:
    Base.metadata.create_all(bind=get_engine(settings))


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
