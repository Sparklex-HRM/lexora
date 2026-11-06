"""Lexora HRM — async DB engine factory (one shared Postgres, row-level org_id)."""
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from shared.utils.config import settings


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str = None):
    return create_async_engine(
        database_url or settings.DATABASE_URL,
        echo=False,
        pool_size=settings.POOL_SIZE,
        max_overflow=settings.MAX_OVERFLOW,
        pool_timeout=settings.POOL_TIMEOUT,
        pool_recycle=settings.POOL_RECYCLE,
        pool_pre_ping=True,
    )


def make_sessionmaker(engine):
    return async_sessionmaker(engine, expire_on_commit=False)
