import hashlib
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal, Protocol

from redis import Redis

from rag_app.config import config

logger = logging.getLogger(__name__)

CacheStatus = Literal["hit", "miss", "unavailable"]


class RedisClient(Protocol):
    def get(self, key: str) -> str | bytes | None: ...

    def set(
        self,
        key: str,
        value: str | int,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> Any: ...

    def incr(self, key: str) -> int: ...


@dataclass(frozen=True)
class CacheLookup:
    status: CacheStatus
    value: dict[str, Any] | None = None
    version: int | None = None


class RedisAnswerCache:
    def __init__(
        self,
        client: RedisClient,
        collection_name: str,
        ttl_seconds: int,
    ) -> None:
        self.client = client
        self.collection_name = collection_name
        self.ttl_seconds = ttl_seconds

    @property
    def index_version_key(self) -> str:
        return f"rag:index-version:{self.collection_name}"

    def get_index_version(self) -> int | None:
        try:
            value = self.client.get(self.index_version_key)
            if value is None:
                self.client.set(self.index_version_key, 1, nx=True)
                value = self.client.get(self.index_version_key)

            if value is None:
                return None

            return int(value)
        except Exception as exc:
            logger.warning(
                "answer_cache.index_version unavailable error_type=%s",
                type(exc).__name__,
            )
            return None

    def build_answer_key(
        self,
        question: str,
        options: dict[str, Any],
        *,
        version: int | None,
    ) -> str:
        canonical_value = json.dumps(
            {"question": question, "options": options},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(canonical_value.encode("utf-8")).hexdigest()
        return (
            f"rag:answer:v1:{self.collection_name}:"
            f"{version}:{digest}"
        )

    def get_answer(
        self,
        question: str,
        options: dict[str, Any],
    ) -> CacheLookup:
        version = self.get_index_version()
        if version is None:
            return CacheLookup(status="unavailable")

        key = self.build_answer_key(question, options, version=version)
        try:
            value = self.client.get(key)
            if value is None:
                return CacheLookup(status="miss", version=version)

            if isinstance(value, bytes):
                value = value.decode("utf-8")

            decoded = json.loads(value)
            if (
                not isinstance(decoded, dict)
                or not isinstance(decoded.get("answer"), str)
                or not isinstance(decoded.get("sources"), list)
            ):
                return CacheLookup(status="miss", version=version)

            return CacheLookup(status="hit", value=decoded, version=version)
        except json.JSONDecodeError:
            logger.warning("answer_cache.get invalid_json")
            return CacheLookup(status="miss", version=version)
        except Exception as exc:
            logger.warning(
                "answer_cache.get unavailable error_type=%s",
                type(exc).__name__,
            )
            return CacheLookup(status="unavailable")

    def set_answer(
        self,
        question: str,
        options: dict[str, Any],
        response: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> bool:
        current_version = self.get_index_version()
        if current_version is None:
            return False
        if (
            expected_version is not None
            and current_version != expected_version
        ):
            logger.info(
                "answer_cache.set skipped reason=index_version_changed"
            )
            return False

        key = self.build_answer_key(
            question,
            options,
            version=current_version,
        )
        value = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        try:
            self.client.set(key, value, ex=self.ttl_seconds)
            return True
        except Exception as exc:
            logger.warning(
                "answer_cache.set unavailable error_type=%s",
                type(exc).__name__,
            )
            return False

    def bump_index_version(self) -> int | None:
        try:
            current_version = self.get_index_version()
            if current_version is None:
                return None
            return int(self.client.incr(self.index_version_key))
        except Exception as exc:
            logger.warning(
                "answer_cache.invalidate unavailable error_type=%s",
                type(exc).__name__,
            )
            return None


@lru_cache(maxsize=1)
def get_answer_cache() -> RedisAnswerCache | None:
    if not config.REDIS_URL or not config.COLLECTION_NAME:
        return None

    client = Redis.from_url(
        config.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=config.REDIS_TIMEOUT_SECONDS,
        socket_timeout=config.REDIS_TIMEOUT_SECONDS,
    )
    return RedisAnswerCache(
        client=client,
        collection_name=config.COLLECTION_NAME,
        ttl_seconds=config.ANSWER_CACHE_TTL_SECONDS,
    )
