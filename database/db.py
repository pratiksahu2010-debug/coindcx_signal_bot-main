"""Async SQLAlchemy database lifecycle with production PostgreSQL enforcement."""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from config import settings
from utils.logger import logger


class Base(DeclarativeBase):
    pass


engine = None
SessionLocal = None


async def init_db() -> None:
    global engine, SessionLocal
    # Import models before create_all so every persistent table is registered.
    from database import models  # noqa: F401
    url = settings.database_url
    # Render's local filesystem is ephemeral on the current service plan.
    # Refuse production scans with SQLite so deduplication and retests survive restarts.
    if settings.is_production and not settings.use_postgres:
        raise RuntimeError(
            "Production requires DATABASE_URL pointing to PostgreSQL; "
            "SQLite is permitted only for local development."
        )
    if settings.use_postgres:
        # Render commonly supplies postgres:// or postgresql:// URLs; use the
        # asyncpg driver already included in requirements.
        if url.startswith("postgres://"):
            url = "postgresql+asyncpg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        url = url.replace("sslmode=require", "ssl=require")
        engine = create_async_engine(
            url,
            pool_size=2,
            max_overflow=3,
            pool_pre_ping=True,
            pool_recycle=300,
        )
    else:
        engine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            # Give short concurrent writes from the Normal and Momentum engines
            # time to complete instead of failing immediately with SQLITE_BUSY.
            connect_args={"timeout": 30},
        )
        logger.warning(
            "Using SQLite. State and deduplication work while this Render instance "
            "is running, but the Free filesystem is ephemeral and state can reset "
            "after a restart or redeploy."
        )

    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.run_sync(Base.metadata.create_all)
            existing = await conn.run_sync(
                lambda sync_conn: __import__("sqlalchemy").inspect(sync_conn).get_table_names()
            )
            required = set(Base.metadata.tables)
            missing = sorted(required - set(existing))
            if missing:
                raise RuntimeError(f"Database schema check failed; missing tables: {missing}")
            await conn.execute(text("DELETE FROM signals WHERE engine = 'signals'"))
            await conn.execute(text("DELETE FROM watchlist WHERE engine = 'signals'"))
            await conn.execute(text("DELETE FROM signal_dedup WHERE dedup_key LIKE 'signals:%'"))
            await conn.execute(text("DROP TABLE IF EXISTS step_channel_state"))
        logger.info("DATABASE_READY dialect=%s connectivity=ok schema=ok tables=%d", engine.dialect.name, len(required))
    except Exception:
        logger.exception("DATABASE_STARTUP_CHECK_FAILED connectivity_or_schema=true")
        await engine.dispose()
        engine = None
        SessionLocal = None
        raise


async def get_session() -> AsyncSession:
    assert SessionLocal is not None
    async with SessionLocal() as session:
        yield session

async def dispose_db() -> None:
    """Close the SQLAlchemy pool cleanly during application shutdown."""
    global engine, SessionLocal
    if engine is not None:
        await engine.dispose()
    engine = None
    SessionLocal = None
