import unittest

from app.agent.foundation.subgraphs.worldline.retrieval import WorldLineContextAdapter
from app.rag.context_packing import (
    ContextPackBuilder,
    EvidenceSufficiencyStatus,
    load_context_packing_config,
)
from app.rag.query_understanding.models import (
    QueryEntityStatus,
    QueryType,
    QueryVariant,
    QueryVariantKind,
    ResolvedQueryEntity,
    RetrievalStrategy,
    SearchChannel,
)
from app.rag.knowledge.entity_linking.models import EntityType
from app.rag.retrieval import RetrievalResult, RetrievalStatus
from tests.reranking_helpers import candidate, candidate_pool, plan, profile


def decomposed_plan():
    base = plan()
    return base.model_copy(
        update={
            "queries": [
                *base.queries,
                QueryVariant(
                    text="晴天由谁作曲",
                    kind=QueryVariantKind.DECOMPOSED,
                    channel=SearchChannel.BOTH,
                    weight=0.9,
                ),
            ],
            "strategy": RetrievalStrategy.MULTI_QUERY_HYBRID,
        }
    )


def with_subquery(index: int, subquery_id: str):
    value = candidate(index)
    evidence = value.channel_evidence[0].model_copy(
        update={
            "subquery_id": subquery_id,
            "retrieval_leg": f"{subquery_id}:dense",
            "variant_index": 1,
            "variant_kind": QueryVariantKind.DECOMPOSED,
        }
    )
    return value.model_copy(update={"channel_evidence": [evidence]})


class RAGContextPackingTests(unittest.TestCase):
    def test_config_freezes_current_context_boundaries(self) -> None:
        config = load_context_packing_config()

        self.assertEqual(config.context_version, "v1")
        self.assertEqual(config.selection.max_items, 8)
        self.assertEqual(config.selection.token_budget, 6_000)
        self.assertEqual(config.compression.max_item_tokens, 1_200)

    def test_pack_extracts_child_span_and_compresses_parent_with_lineage(self) -> None:
        pack = ContextPackBuilder().build(plan(), candidate_pool(12))

        self.assertEqual(
            pack.sufficiency.status,
            EvidenceSufficiencyStatus.SUFFICIENT,
        )
        self.assertEqual(len(pack.items), 8)
        self.assertEqual(pack.items[0].parent_id, "parent-1")
        self.assertIn(pack.items[0].exact_text, pack.items[0].context)
        self.assertEqual(pack.items[0].subquery_ids, ["q0:original"])
        self.assertLessEqual(pack.token_count, pack.token_budget)

    def test_pack_prioritizes_required_decomposed_query_coverage(self) -> None:
        query_plan = decomposed_plan()
        result = RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=query_plan.plan_id,
            index_profile=profile(),
            candidates=[candidate(1), with_subquery(2, "q1:decomposed")],
        )

        pack = ContextPackBuilder().build(query_plan, result)

        self.assertEqual(
            pack.sufficiency.status,
            EvidenceSufficiencyStatus.SUFFICIENT,
        )
        self.assertEqual(pack.items[0].parent_id, "parent-2")
        self.assertEqual(
            pack.sufficiency.required_subquery_ids,
            ["q1:decomposed"],
        )

    def test_gate_blocks_missing_decomposed_query_coverage(self) -> None:
        query_plan = decomposed_plan()
        result = RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=query_plan.plan_id,
            index_profile=profile(),
            candidates=[candidate(1)],
        )

        pack = ContextPackBuilder().build(query_plan, result)
        documents = WorldLineContextAdapter().adapt(result, query_plan)

        self.assertEqual(
            pack.sufficiency.status,
            EvidenceSufficiencyStatus.INSUFFICIENT,
        )
        self.assertEqual(
            pack.sufficiency.missing_subquery_ids,
            ["q1:decomposed"],
        )
        self.assertEqual(documents, [])

    def test_gate_requires_both_entities_for_comparison(self) -> None:
        base = plan()
        query_plan = base.model_copy(
            update={
                "query_types": [QueryType.COMPARISON],
                "entities": [
                    ResolvedQueryEntity(
                        surface=name,
                        canonical_name=name,
                        entity_type=EntityType.ARTIST,
                        knowledge_entity_id=entity_id,
                        status=QueryEntityStatus.RESOLVED,
                        confidence=1,
                    )
                    for name, entity_id in (
                        ("周杰伦", "artist:jay_chou"),
                        ("林俊杰", "artist:jj_lin"),
                    )
                ],
            }
        )
        only_jay = candidate(1).model_copy(
            update={"entity_ids": ["artist:jay_chou"]}
        )
        result = RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=query_plan.plan_id,
            index_profile=profile(),
            candidates=[only_jay],
        )

        pack = ContextPackBuilder().build(query_plan, result)

        self.assertEqual(
            pack.sufficiency.status,
            EvidenceSufficiencyStatus.INSUFFICIENT,
        )
        self.assertEqual(pack.sufficiency.missing_entity_ids, ["artist:jj_lin"])

    def test_gate_only_records_entity_coverage_required_by_the_query(self) -> None:
        result = RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=plan().plan_id,
            index_profile=profile(),
            candidates=[
                candidate(index).model_copy(
                    update={"entity_ids": [f"artist:unrelated-{index}"]}
                )
                for index in range(1, 26)
            ],
        )

        pack = ContextPackBuilder().build(plan(), result)

        self.assertEqual(
            pack.sufficiency.status,
            EvidenceSufficiencyStatus.SUFFICIENT,
        )
        self.assertEqual(pack.sufficiency.covered_entity_ids, [])

    def test_pack_removes_near_duplicate_evidence(self) -> None:
        first = candidate(1)
        duplicate = candidate(2).model_copy(
            update={"primary_child_content": first.primary_child_content}
        )
        result = RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=plan().plan_id,
            index_profile=profile(),
            candidates=[first, duplicate],
        )

        pack = ContextPackBuilder().build(plan(), result)

        self.assertEqual(len(pack.items), 1)
        self.assertIn("near_duplicate_evidence_removed:1", pack.warnings)

    def test_worldline_adapter_exposes_context_pack_and_evidence_metadata(self) -> None:
        result = candidate_pool(2)

        documents = WorldLineContextAdapter().adapt(result, plan())

        self.assertEqual(len(documents), 2)
        self.assertEqual(documents[0].metadata["context_version"], "v1")
        self.assertEqual(documents[0].metadata["sufficiency_status"], "sufficient")
        self.assertEqual(
            documents[0].metadata["evidence_span"],
            "第 1 条 Child 命中证据",
        )
        self.assertIn("第 1 条 Child 命中证据", documents[0].content)


if __name__ == "__main__":
    unittest.main()
