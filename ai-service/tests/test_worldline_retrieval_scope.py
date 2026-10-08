from unittest.mock import AsyncMock

import pytest

from app.agent.foundation.subgraphs.worldline.retrieval import (
    HybridMusicKnowledgeRetriever,
)
from app.agent.foundation.subgraphs.worldline.retrieval_scope import (
    root_knowledge_id,
    scope_root_plan,
)
from app.rag.retrieval import RetrievalResult, RetrievalStatus
from tests.test_worldline_retrieval_adapter import (
    FakeReranker,
    candidate,
    entity,
    plan,
    profile,
)


@pytest.mark.asyncio
async def test_unmapped_catalog_root_never_runs_global_search_or_rerank():
    provider, reranker = AsyncMock(), AsyncMock()
    unknown = entity().model_copy(
        update={
            "entity_id": "netease:artist:6452",
            "name": "周杰伦",
            "entity_type": "artist",
        }
    )
    assert (
        await HybridMusicKnowledgeRetriever(
            provider, reranker=reranker, rerank_mode="on"
        ).retrieve(unknown, plan())
        == []
    )
    provider.retrieve.assert_not_awaited()
    reranker.rerank.assert_not_awaited()


def test_root_filter_preserves_existing_constraints_and_changes_plan_identity():
    original = plan()
    scoped = scope_root_plan(original, "song:qing_tian")
    assert scoped.plan_id != original.plan_id
    assert scoped.plan_id == scope_root_plan(original, "song:qing_tian").plan_id
    assert scoped.plan_id != scope_root_plan(original, "song:other").plan_id
    assert scoped.metadata_filter.root.children[0].value == "song:qing_tian"
    assert scoped.original_query == original.original_query
    assert scoped.queries == original.queries
    assert root_knowledge_id(entity(), original) == "song:qing_tian"


@pytest.mark.asyncio
async def test_unrelated_high_ranked_candidates_are_removed_before_rerank():
    provider, reranker = AsyncMock(), AsyncMock(wraps=FakeReranker())
    unrelated = candidate(1).model_copy(update={"entity_ids": ["artist:nirvana"]})
    related = candidate(2)

    async def retrieve(scoped):
        return RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=scoped.plan_id,
            index_profile=profile(),
            candidates=[unrelated, related],
        )

    provider.retrieve.side_effect = retrieve
    retriever = HybridMusicKnowledgeRetriever(
        provider, reranker=reranker, rerank_mode="on"
    )
    documents = await retriever.retrieve(entity(), plan())
    assert [item.id for item in documents] == ["parent-2"]
    assert [
        item.parent_id for item in reranker.rerank.await_args.args[1].candidates
    ] == ["parent-2"]


@pytest.mark.asyncio
async def test_empty_or_unavailable_scoped_results_do_not_fabricate_sources():
    provider = AsyncMock()

    async def retrieve(scoped):
        return RetrievalResult(
            status=RetrievalStatus.EMPTY,
            plan_id=scoped.plan_id,
            index_profile=profile(),
        )

    provider.retrieve.side_effect = retrieve
    assert (
        await HybridMusicKnowledgeRetriever(provider).retrieve(entity(), plan()) == []
    )
