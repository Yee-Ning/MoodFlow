"""Popularity read contract, partial failure and Redis-only derived caching."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.redis_reuse import RedisReuseConfig
from app.tools.music import MusicServiceClient, MusicServiceError
from app.tools.popularity import (
    POPULARITY_TTL_SECONDS, PopularityReadLimiter, RedisPopularityCache,
    get_song_popularity,
)
from tests.test_redis_budget import redis_server

__all__ = ["redis_server"]


class CacheTransport:
    def __init__(self):
        self.values, self.writes = {}, []

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, *, ex):
        self.values[key] = value
        self.writes.append((key, value, ex))


@pytest.fixture
def cache():
    return RedisPopularityCache(RedisReuseConfig("redis://unused"), client=CacheTransport())


@pytest.mark.asyncio
async def test_batch_success_deduplicates_and_caches_real_zero(cache):
    client = SimpleNamespace(get_song_comment_count=AsyncMock(side_effect=[120, 0, 240]))
    counts = await get_song_popularity([1, 2, 1, 3], client=client, cache=cache,
                                       limiter=PopularityReadLimiter(0))
    assert counts == {1: 120, 2: 0, 3: 240}
    assert client.get_song_comment_count.await_count == 3
    assert {row[2] for row in cache.client.writes} == {604800}
    assert all(key.endswith(":netease-comment-total-v1-popularity-v1") for key in cache.client.values)
    assert all("comment_count" in json.loads(raw) for raw in cache.client.values.values())


@pytest.mark.asyncio
async def test_single_failure_is_unknown_does_not_retry_or_poison_other_songs(cache):
    async def read(song_id):
        if song_id == 2:
            raise MusicServiceError("http_502", "unavailable")
        return {1: 100, 3: 300}[song_id]

    client = SimpleNamespace(get_song_comment_count=AsyncMock(side_effect=read))
    counts = await get_song_popularity([1, 2, 3], client=client, cache=cache,
                                       limiter=PopularityReadLimiter(0))
    assert counts == {1: 100, 3: 300}
    assert counts.get(2) is None and cache.load(2) is None
    assert client.get_song_comment_count.await_count == 3


@pytest.mark.asyncio
async def test_cache_hit_and_mixed_miss_only_read_missing_song(cache):
    cache.save(1, 0)
    cache.save(2, 200)
    client = SimpleNamespace(get_song_comment_count=AsyncMock(return_value=300))
    assert await get_song_popularity([1, 2, 3], client=client, cache=cache,
                                     limiter=PopularityReadLimiter(0)) == {1: 0, 2: 200, 3: 300}
    client.get_song_comment_count.assert_awaited_once_with(3)
    client.get_song_comment_count.reset_mock()
    assert await get_song_popularity([1, 2, 3], client=client, cache=cache,
                                     limiter=PopularityReadLimiter(0)) == {1: 0, 2: 200, 3: 300}
    client.get_song_comment_count.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [None, -1, True, "100", 1.5])
async def test_invalid_api_total_is_unknown_not_zero_or_cached(cache, count):
    client = SimpleNamespace(get_song_comment_count=AsyncMock(return_value=count))
    assert await get_song_popularity([1], client=client, cache=cache,
                                     limiter=PopularityReadLimiter(0)) == {}
    assert not cache.client.writes


@pytest.mark.asyncio
async def test_429_stops_new_api_reads_and_retains_already_known_cache_results(cache):
    cache.save(3, 300)
    client = SimpleNamespace(get_song_comment_count=AsyncMock(
        side_effect=MusicServiceError("http_429", "quota exceeded")))
    assert await get_song_popularity([1, 2, 3], client=client, cache=cache,
                                     limiter=PopularityReadLimiter(0)) == {3: 300}
    client.get_song_comment_count.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_empty_and_invalid_batches_do_not_dispatch(cache):
    client = SimpleNamespace(get_song_comment_count=AsyncMock())
    assert await get_song_popularity([], client=client, cache=cache) == {}
    for values in ([1, True], ["1"], [-1], list(range(1, 62))):
        with pytest.raises(ValueError):
            await get_song_popularity(values, client=client, cache=cache)
    client.get_song_comment_count.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_batches_share_serialized_read_quota(cache):
    active, maximum = 0, 0

    async def read(song_id):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.005)
        active -= 1
        return song_id * 100

    client = SimpleNamespace(get_song_comment_count=AsyncMock(side_effect=read))
    limiter = PopularityReadLimiter(0.001)
    rows = await asyncio.gather(*(get_song_popularity([identity], client=client, cache=cache,
                                                    limiter=limiter) for identity in (1, 2, 3)))
    assert rows == [{1: 100}, {2: 200}, {3: 300}] and maximum == 1


@pytest.mark.asyncio
async def test_music_client_reuses_request_wrapper_and_checks_identity(monkeypatch):
    request = AsyncMock(return_value={"code": 200, "message": "ok", "data": {"songId": 1, "commentCount": 123}})
    client = MusicServiceClient()
    monkeypatch.setattr(client, "_request", request)
    assert await client.get_song_comment_count(1) == 123
    request.assert_awaited_once_with("GET", "/api/songs/1/comment-summary")
    request.return_value["data"]["songId"] = 2
    with pytest.raises(MusicServiceError, match="不一致"):
        await client.get_song_comment_count(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{"songId": 1}, {"songId": 1, "commentCount": "2"},
                                  {"songId": 1, "commentCount": -1}, {"songId": 1, "commentCount": True}])
async def test_music_client_rejects_missing_or_coerced_total(monkeypatch, data):
    client = MusicServiceClient()
    monkeypatch.setattr(client, "_request", AsyncMock(return_value={"code": 200, "message": "ok", "data": data}))
    with pytest.raises(MusicServiceError) as error:
        await client.get_song_comment_count(1)
    assert error.value.code == "invalid_response"


def test_corrupt_cache_is_rejected_and_real_redis_ttl_is_seven_days(cache, redis_server):
    for raw in ('{"song_id":1,"comment_count":null}', '{"song_id":2,"comment_count":100}',
                '{"song_id":1,"comment_count":true}', "not-json"):
        cache.client.values[cache.key(1)] = raw
        assert cache.load(1) is None
    live = RedisPopularityCache(RedisReuseConfig("redis://unused"), client=redis_server)
    live.save(185809, 68320)
    assert live.load(185809) == 68320
    assert POPULARITY_TTL_SECONDS - 2 <= redis_server.ttl(live.key(185809)) <= POPULARITY_TTL_SECONDS
    assert live.key(185809) != live.key(186016)
