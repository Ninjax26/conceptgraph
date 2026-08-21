from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints.exam import router as exam_router
from app.api.endpoints.ingest import router as ingest_router
from app.api.endpoints.query import router as query_router
from app.core.database import close_database_connections, initialize_database_schema
from app.core.database import postgres_engine
from app.core.config import settings
from sqlalchemy import text

LOCAL_DEV_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await initialize_database_schema()
    yield
    await close_database_connections()


app = FastAPI(title="ConceptGraph", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://0.0.0.0:3000",
        "http://0.0.0.0:5173",
        *settings.configured_cors_origins,
    ],
    allow_origin_regex=LOCAL_DEV_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(query_router)
app.include_router(exam_router)
app.include_router(ingest_router)


@app.get("/api/v1/health", tags=["system"])
async def health_check() -> dict[str, str]:
    try:
        async with postgres_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is unavailable.") from exc
    return {"status": "healthy"}
