from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints.exam import router as exam_router
from app.api.endpoints.ingest import router as ingest_router
from app.api.endpoints.query import router as query_router
from app.core.database import close_database_connections

LOCAL_DEV_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
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
    ],
    allow_origin_regex=LOCAL_DEV_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(query_router)
app.include_router(exam_router)
app.include_router(ingest_router)
