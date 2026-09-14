import unittest

from pydantic import ValidationError

from app.rag.enrichment.models import ContentType
from app.rag.knowledge.entity_linking.models import EntityType
from app.rag.query_understanding import (
    ContextDependency,
    ContextResolution,
    ContextResolutionStatus,
    QueryAnalysis,
    QueryConstraints,
    QueryType,
    build_search_plan,
    load_query_understanding_config,
)
from app.rag.query_understanding.models import (
    FilterCondition,
    FilterOperator,
    MetadataField,
    QueryEntityStatus,
    ResolvedQueryEntity,
)


def analysis(**updates):  # type: ignore[no-untyped-def]
    context = ContextResolution(
        status=ContextResolutionStatus.RESOLVED,
        dependency=ContextDependency.STANDALONE,
        original_query="周杰伦 2000 到 2005 年的音乐风格如何变化",
        standalone_query="周杰伦 2000 到 2005 年的音乐风格如何变化",
    )
    entity = ResolvedQueryEntity(
        surface="周杰伦",
        canonical_name="周杰伦",
        entity_type=EntityType.ARTIST,
        knowledge_entity_id="artist:jay_chou",
        aliases=["周杰倫", "Jay Chou", "周董"],
        status=QueryEntityStatus.RESOLVED,
        confidence=0.99,
    )
    payload = {
        "original_query": context.original_query,
        "standalone_query": context.standalone_query,
        "query_types": [QueryType.TIMELINE, QueryType.INFLUENCE_STYLE],
        "entities": [entity],
        "constraints": QueryConstraints(
            entity_ids=["artist:jay_chou"],
            entity_types=[EntityType.ARTIST],
            start_year=2000,
            end_year=2005,
            content_types=[ContentType.MUSIC_STYLE],
        ),
        "context": context,
        "confidence": 0.98,
    }
    payload.update(updates)
    return QueryAnalysis.model_validate(payload)


class QueryUnderstandingContractTests(unittest.TestCase):
    def test_query_constraints_require_complete_ordered_year_range(self) -> None:
        with self.assertRaisesRegex(ValidationError, "both bounds"):
            QueryConstraints(start_year=2000)
        with self.assertRaisesRegex(ValidationError, "must not exceed"):
            QueryConstraints(start_year=2005, end_year=2000)

    def test_filter_conditions_reject_operator_payload_mismatch(self) -> None:
        with self.assertRaisesRegex(ValidationError, "match_any"):
            FilterCondition(
                field=MetadataField.ENTITY_IDS,
                operator=FilterOperator.MATCH_ANY,
                value=[],
            )

    def test_plan_is_stable_bounded_and_uses_only_whitelisted_fields(self) -> None:
        config = load_query_understanding_config()
        first = build_search_plan(analysis(), config)
        second = build_search_plan(analysis(), config)

        self.assertEqual(first.plan_id, second.plan_id)
        self.assertLessEqual(len(first.queries), config.max_query_variants)
        self.assertEqual(first.strategy.value, "multi_query_hybrid")
        self.assertFalse(any(item.kind.value == "hyde" for item in first.queries))
        self.assertIsNotNone(first.metadata_filter.root)
        encoded = first.metadata_filter.model_dump_json()
        self.assertIn('"entity_ids"', encoded)
        self.assertIn('"start_year"', encoded)
        self.assertNotIn('"source_type"', encoded)

    def test_relationship_entities_are_required_individually(self) -> None:
        base = analysis()
        second = base.entities[0].model_copy(
            update={
                "surface": "方文山",
                "canonical_name": "方文山",
                "entity_type": EntityType.PERSON,
                "knowledge_entity_id": "person:vincent_fang",
                "aliases": ["Vincent Fang"],
            }
        )
        relationship = analysis(
            query_types=[QueryType.RELATIONSHIP],
            entities=[base.entities[0], second],
            constraints=QueryConstraints(
                entity_ids=["artist:jay_chou", "person:vincent_fang"],
                entity_types=[EntityType.ARTIST, EntityType.PERSON],
            ),
        )

        plan = build_search_plan(relationship, load_query_understanding_config())
        encoded = plan.metadata_filter.model_dump_json()

        self.assertEqual(encoded.count('"match_value"'), 2)

    def test_hyde_cannot_be_enabled_in_v1(self) -> None:
        config = load_query_understanding_config().model_copy(
            update={"enable_hyde": True}
        )

        with self.assertRaisesRegex(ValueError, "intentionally unsupported"):
            build_search_plan(analysis(), config)


if __name__ == "__main__":
    unittest.main()
