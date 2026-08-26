from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import time

from redis.asyncio import Redis, from_url

from app.core.config import Settings, settings


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class DemoAccessService:
    def __init__(self, config: Settings = settings) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.demo_access_token_value is not None

    def verify_access_token(self, candidate: str | None) -> bool:
        expected = self.config.demo_access_token_value
        if expected is None or candidate is None:
            return False
        return hmac.compare_digest(candidate, expected)

    def issue_cookie(self, *, now: int | None = None) -> str:
        timestamp = str(now if now is not None else int(time.time()))
        return f"{timestamp}.{self._sign(timestamp)}"

    def verify_cookie(self, cookie_value: str | None, *, now: int | None = None) -> bool:
        if not cookie_value or not self.enabled:
            return False
        try:
            timestamp, signature = cookie_value.split(".", maxsplit=1)
            issued_at = int(timestamp)
        except (TypeError, ValueError):
            return False

        current_time = now if now is not None else int(time.time())
        if issued_at > current_time + 30:
            return False
        if current_time - issued_at > self.config.auth_session_ttl_seconds:
            return False
        return hmac.compare_digest(signature, self._sign(timestamp))

    @staticmethod
    def fingerprint(credential: str) -> str:
        return hashlib.sha256(credential.encode("utf-8")).hexdigest()[:24]

    def _sign(self, timestamp: str) -> str:
        secret = self.config.demo_access_token_value
        if secret is None:
            raise RuntimeError("Demo access protection is not configured.")
        return hmac.new(
            secret.encode("utf-8"),
            timestamp.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()


class RateLimitService:
    def __init__(self, redis_url: str = settings.redis_url) -> None:
        self.redis_url = redis_url
        self._client: Redis | None = None

    async def check(
        self,
        key: str,
        limit: int,
        *,
        now: int | None = None,
    ) -> RateLimitResult:
        current_time = now if now is not None else int(time.time())
        window = current_time // 60
        redis_key = f"conceptgraph:rate:{key}:{window}"
        pipeline = self.client.pipeline(transaction=True)
        pipeline.incr(redis_key)
        pipeline.expire(redis_key, 120)
        count, _ = await pipeline.execute()
        count = int(count)
        return RateLimitResult(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            retry_after=max(1, 60 - (current_time % 60)),
        )

    @property
    def client(self) -> Redis:
        if self._client is None:
            self._client = from_url(self.redis_url, decode_responses=True)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


demo_access_service = DemoAccessService()
rate_limit_service = RateLimitService()
