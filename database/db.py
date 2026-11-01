"""Database session management — PostgreSQL preferred, SQLite for local."""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from config import settings
from utils.logger import logger


class Base(DeclarativeBase):
    pass


engine = None
SessionLocal = None


async def init_db() -> None:
    global engine, SessionLocal
    url = settings.database_url
    if settings.use_postgres:
        engine = create_async_engine(
            url,
            pool_size=2,
            max_overflow=3,
            pool_pre_ping=True,
            pool_recycle=300,
        )
    else:
        engine = create_async_engine(url, echo=False)
        logger.warning("Using SQLite — production persistence not guaranteed on Render Free")

    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized")


async def get_session() -> AsyncSession:
    assert SessionLocal is not None
    async with SessionLocal() as session:
        yield session