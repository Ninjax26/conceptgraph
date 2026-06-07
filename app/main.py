from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from app.api.endpoints.query import router as query_router
from app.core.database import close_database_connections


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    await close_database_connections()


app = FastAPI(title="ConceptGraph", lifespan=lifespan)
app.include_router(query_router)
