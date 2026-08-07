import json
from typing import Any

from rag_app.infrastructure.answer_cache import RedisAnswerCache


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[dict[str, Any]] = []

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(
        self,
        key: str,
        value: str | int,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if nx and key in self.values:
            return False

        self.values[key] = str(value)
        self.set_calls.append({"key": key, "value": str(value), "ex": ex, "nx": nx})
        return True

    def incr(self, key: str) -> int:
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value


class FailingRedis(FakeRedis):
    def get(self, key: str) -> str | None:
        raise ConnectionError("redis unavailable")

    def set(
        self,
        key: str,
        value: str | int,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        raise ConnectionError("redis unavailable")

    def incr(self, key: str) -> int:
        raise ConnectionError("redis unavailable")


def make_cache(client: FakeRedis) -> RedisAnswerCache:
    return RedisAnswerCache(
        client=client,
        collection_name="documents",
        ttl_seconds=3600,
    )


def test_cache_round_trip_uses_ttl_and_does_not_expose_question_in_key() -> None:
    client = FakeRedis()
    cache = make_cache(client)
    options = {"top_k": 7, "search_type": "similarity"}
    response = {
        "answer": "LangChain is an orchestration framework.",
        "sources": [{"source": "langchain.md", "section_path": "", "snippet": "..."}],
    }

    miss = cache.get_answer("What is LangChain?", options)
    cache.set_answer("What is LangChain?", options, response)
    hit = cache.get_answer("What is LangChain?", options)

    assert miss.status == "miss"
    assert hit.status == "hit"
    assert hit.value == response
    answer_write = next(call for call in client.set_calls if call["ex"] == 3600)
    assert "What is LangChain?" not in answer_write["key"]
    assert answer_write["key"].startswith("rag:answer:v1:documents:1:")


def test_retrieval_configuration_is_part_of_the_cache_key() -> None:
    cache = make_cache(FakeRedis())
    response = {"answer": "answer", "sources": []}

    cache.set_answer("question", {"top_k": 7}, response)

    assert cache.get_answer("question", {"top_k": 8}).status == "miss"


def test_bumping_index_version_invalidates_previous_answers() -> None:
    cache = make_cache(FakeRedis())
    response = {"answer": "old answer", "sources": []}
    options = {"top_k": 7}
    cache.set_answer("question", options, response)

    new_version = cache.bump_index_version()

    assert new_version == 2
    assert cache.get_answer("question", options).status == "miss"


def test_redis_failure_is_reported_as_unavailable_instead_of_raising() -> None:
    cache = make_cache(FailingRedis())

    lookup = cache.get_answer("question", {"top_k": 7})

    assert lookup.status == "unavailable"
    assert cache.set_answer(
        "question",
        {"top_k": 7},
        {"answer": "answer", "sources": []},
    ) is False
    assert cache.bump_index_version() is None


def test_invalid_json_is_treated_as_a_cache_miss() -> None:
    client = FakeRedis()
    cache = make_cache(client)
    options = {"top_k": 7}
    version = cache.get_index_version()
    key = cache.build_answer_key("question", options, version=version)
    client.values[key] = "not-json"

    lookup = cache.get_answer("question", options)

    assert lookup.status == "miss"
    assert lookup.value is None
    assert json.loads(json.dumps(options)) == options


def test_structurally_invalid_cached_response_is_treated_as_a_miss() -> None:
    client = FakeRedis()
    cache = make_cache(client)
    options = {"top_k": 7}
    version = cache.get_index_version()
    key = cache.build_answer_key("question", options, version=version)
    client.values[key] = json.dumps({"answer": "missing sources"})

    lookup = cache.get_answer("question", options)

    assert lookup.status == "miss"


def test_answer_is_not_cached_when_index_version_changed_during_generation() -> None:
    client = FakeRedis()
    cache = make_cache(client)
    options = {"top_k": 7}
    lookup = cache.get_answer("question", options)
    cache.bump_index_version()

    stored = cache.set_answer(
        "question",
        options,
        {"answer": "stale answer", "sources": []},
        expected_version=lookup.version,
    )

    assert stored is False
    assert cache.get_answer("question", options).status == "miss"
