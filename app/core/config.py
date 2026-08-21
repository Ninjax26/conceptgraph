from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    postgres_user: str = Field(default="conceptgraph", alias="POSTGRES_USER")
    postgres_password: str = Field(default="conceptgraph_password", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="conceptgraph", alias="POSTGRES_DB")
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_username: str = Field(default="neo4j", alias="NEO4J_USERNAME")
    neo4j_password: str = Field(default="conceptgraph_password", alias="NEO4J_PASSWORD")

    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    qdrant_collection_name: str = Field(
        default="conceptgraph_chunks",
        alias="QDRANT_COLLECTION_NAME",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    cors_allowed_origins: str = Field(default="", alias="CORS_ALLOWED_ORIGINS")

    object_storage_backend: str = Field(default="s3", alias="OBJECT_STORAGE_BACKEND")
    s3_bucket: str = Field(default="conceptgraph-pdfs", alias="S3_BUCKET")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")
    s3_endpoint_url: str | None = Field(
        default="http://localhost:9000",
        alias="S3_ENDPOINT_URL",
    )
    s3_access_key_id: str | None = Field(
        default="conceptgraph",
        alias="S3_ACCESS_KEY_ID",
    )
    s3_secret_access_key: str | None = Field(
        default="conceptgraph_local_only",
        alias="S3_SECRET_ACCESS_KEY",
    )
    s3_force_path_style: bool = Field(default=True, alias="S3_FORCE_PATH_STYLE")
    s3_auto_create_bucket: bool = Field(default=True, alias="S3_AUTO_CREATE_BUCKET")
    s3_server_side_encryption: str | None = Field(
        default=None,
        alias="S3_SERVER_SIDE_ENCRYPTION",
    )
    legacy_upload_dir: Path = Field(default=Path("data/uploads"), alias="LEGACY_UPLOAD_DIR")

    embedding_model_name: str = Field(
        default="all-MiniLM-L6-v2",
        alias="EMBEDDING_MODEL_NAME",
    )

    llm_provider: str = Field(default="groq", alias="LLM_PROVIDER")
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    groq_model: str = Field(default="llama-3.1-8b-instant", alias="GROQ_MODEL")
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-1.5-flash", alias="GEMINI_MODEL")
    evidence_min_score: float = Field(default=0.35, ge=0, le=1, alias="EVIDENCE_MIN_SCORE")
    evidence_medium_score: float = Field(default=0.5, ge=0, le=1, alias="EVIDENCE_MEDIUM_SCORE")
    evidence_high_score: float = Field(default=0.7, ge=0, le=1, alias="EVIDENCE_HIGH_SCORE")

    @model_validator(mode="after")
    def validate_evidence_thresholds(self) -> "Settings":
        if not (
            self.evidence_min_score
            <= self.evidence_medium_score
            <= self.evidence_high_score
        ):
            raise ValueError(
                "Evidence thresholds must satisfy min <= medium <= high."
            )
        return self

    @model_validator(mode="after")
    def validate_storage(self) -> "Settings":
        backend = self.object_storage_backend.strip().lower()
        if backend not in {"s3", "local"}:
            raise ValueError("OBJECT_STORAGE_BACKEND must be either 's3' or 'local'.")
        self.object_storage_backend = backend
        if self.s3_endpoint_url is not None:
            self.s3_endpoint_url = self.s3_endpoint_url.strip() or None
        if backend == "s3" and not self.s3_bucket.strip():
            raise ValueError("S3_BUCKET is required when object storage is enabled.")
        return self

    @property
    def postgres_dsn(self) -> str:
        if self.database_url:
            if self.database_url.startswith("postgresql+asyncpg://"):
                return self.database_url
            if self.database_url.startswith("postgresql://"):
                return self.database_url.replace(
                    "postgresql://",
                    "postgresql+asyncpg://",
                    1,
                )
            if self.database_url.startswith("postgres://"):
                return self.database_url.replace(
                    "postgres://",
                    "postgresql+asyncpg://",
                    1,
                )
            raise ValueError("DATABASE_URL must use a PostgreSQL URL.")
        return (
            "postgresql+asyncpg://"
            f"{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def configured_cors_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
