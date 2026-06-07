from collections.abc import AsyncGenerator
from dataclasses import dataclass

from neo4j import AsyncDriver, AsyncGraphDatabase
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


@dataclass(slots=True)
class DatabaseClients:
    postgres: AsyncSession
    neo4j: AsyncDriver
    qdrant: QdrantClient


postgres_engine: AsyncEngine = create_async_engine(
    settings.postgres_dsn,
    pool_pre_ping=True,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=postgres_engine,
    expire_on_commit=False,
    autoflush=False,
)

neo4j_driver: AsyncDriver = AsyncGraphDatabase.driver(
    settings.neo4j_uri,
    auth=(settings.neo4j_username, settings.neo4j_password),
)

qdrant_client: QdrantClient = QdrantClient(url=settings.qdrant_url)


async def get_postgres_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def get_db() -> AsyncGenerator[DatabaseClients, None]:
    async with AsyncSessionLocal() as session:
        yield DatabaseClients(
            postgres=session,
            neo4j=neo4j_driver,
            qdrant=qdrant_client,
        )


async def close_database_connections() -> None:
    await postgres_engine.dispose()
    await neo4j_driver.close()
    qdrant_client.close()
