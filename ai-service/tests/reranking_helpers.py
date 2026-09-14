from app.rag.query_understanding.models import (
    IndexProfile,
    MetadataFilter,
    QueryType,
    QueryVariant,
    QueryVariantKind,
    RetrievalStrategy,
    SearchChannel,
    SearchPlan,
)
from app.rag.retrieval import (
    ChannelEvidence,
    DeferredParentContext,
    RetrievalCandidate,
    RetrievalResult,
    RetrievalStatus,
)


def profile() -> IndexProfile:
    return IndexProfile(
        collection_name="music_knowledge_test",
        source_release_id="knowledge-v2-test",
        index_schema_version="v2",
        projection_version="v2",
        registry_version="v3",
    )


def plan() -> SearchPlan:
    return SearchPlan(
        plan_id="query-plan:0123456789abcdef01234567",
        original_query="晴天的创作背景",
        standalone_query="晴天的创作背景",
        query_types=[QueryType.FACT],
        queries=[
            QueryVariant(
                text="晴天的创作背景",
                kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.BOTH,
                weight=1,
            )
        ],
        metadata_filter=MetadataFilter(),
        strategy=RetrievalStrategy.HYBRID,
        candidate_top_k=40,
        final_top_k=10,
        needs_rerank=True,
        analysis_version="v1",
        prompt_version="v1",
        index_profile=profile(),
    )


def candidate(index: int) -> RetrievalCandidate:
    return RetrievalCandidate(
        point_id=f"point-{index}",
        parent_id=f"parent-{index}",
        doc_id=f"doc-{index}",
        title=f"资料 {index}",
        primary_child_id=f"child-{index}",
        primary_child_content=f"第 {index} 条 Child 命中证据",
        primary_child_breadcrumb=f"晴天 > 创作背景 > {index}",
        primary_child_token_count=100,
        parent_context=DeferredParentContext(
            chunk_id=f"parent-{index}",
            content=f"第 {index} 条尚未展开的 Parent 正文",
            token_count=500,
        ),
        source_id=f"source-{index}",
        source_name="测试知识库",
        fusion_score=1 / (60 + index),
        channel_evidence=[
            ChannelEvidence(
                chunk_id=f"child-{index}",
                subquery_id="q0:original",
                retrieval_leg="q0:original:dense",
                variant_index=0,
                variant_kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.DENSE,
                rank=index,
                raw_score=1 - index / 100,
                weight=1,
            )
        ],
        matched_child_ids=[f"child-{index}"],
    )


def candidate_pool(count: int = 40) -> RetrievalResult:
    return RetrievalResult(
        status=RetrievalStatus.SUCCESS,
        plan_id=plan().plan_id,
        index_profile=profile(),
        candidates=[candidate(index) for index in range(1, count + 1)],
    )
