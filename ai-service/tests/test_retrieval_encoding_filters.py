import unittest

from app.rag.query_understanding.models import (
    FilterCondition,
    FilterGroup,
    FilterOperator,
    MetadataField,
    MetadataFilter,
)
from app.rag.retrieval import (
    QwenQueryEncoder,
    compile_qdrant_filter,
    load_retrieval_config,
)


class CapturingDenseEncoder:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.texts: list[str] = []

    def encode_documents(self, texts) -> list[list[float]]:
        self.texts = list(texts)
        return self.vectors


class RetrievalQueryEncoderTests(unittest.TestCase):
    def test_query_instruction_is_applied_once_to_a_batch(self) -> None:
        config = load_retrieval_config().dense.model_copy(
            update={"dimension": 2, "query_instruction": "Retrieve music facts."}
        )
        backend = CapturingDenseEncoder([[1.0, 0.0], [0.0, 1.0]])
        encoder = QwenQueryEncoder(config, encoder=backend)

        vectors = encoder.encode_queries(["  晴天   创作背景 ", "周杰伦 风格"])

        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(
            backend.texts,
            [
                "Instruct: Retrieve music facts.\nQuery: 晴天 创作背景",
                "Instruct: Retrieve music facts.\nQuery: 周杰伦 风格",
            ],
        )

    def test_embedding_dimension_is_checked_before_qdrant(self) -> None:
        config = load_retrieval_config().dense.model_copy(update={"dimension": 2})
        encoder = QwenQueryEncoder(
            config,
            encoder=CapturingDenseEncoder([[1.0]]),
        )

        with self.assertRaisesRegex(ValueError, "dimension_mismatch"):
            encoder.encode_queries(["晴天"])


class QdrantFilterCompilerTests(unittest.TestCase):
    def test_recursive_groups_preserve_all_and_any_semantics(self) -> None:
        metadata_filter = MetadataFilter(
            root=FilterGroup(
                operator="all",
                children=[
                    FilterCondition(
                        field=MetadataField.ENTITY_IDS,
                        operator=FilterOperator.MATCH_ANY,
                        value=["artist:jay_chou"],
                    ),
                    FilterGroup(
                        operator="any",
                        children=[
                            FilterCondition(
                                field=MetadataField.YEAR,
                                operator=FilterOperator.RANGE,
                                gte=2000,
                                lte=2005,
                            ),
                            FilterCondition(
                                field=MetadataField.START_YEAR,
                                operator=FilterOperator.RANGE,
                                lte=2005,
                            ),
                        ],
                    ),
                ],
            )
        )

        compiled = compile_qdrant_filter(metadata_filter)

        assert compiled is not None
        self.assertEqual(len(compiled.must), 2)
        entity_condition = compiled.must[0]
        self.assertEqual(entity_condition.key, "entity_ids")
        self.assertEqual(entity_condition.match.any, ["artist:jay_chou"])
        temporal_group = compiled.must[1]
        self.assertEqual(len(temporal_group.should), 2)
        self.assertEqual(temporal_group.should[0].range.gte, 2000)
        self.assertEqual(temporal_group.should[0].range.lte, 2005)

    def test_empty_filter_compiles_to_none(self) -> None:
        self.assertIsNone(compile_qdrant_filter(MetadataFilter()))


if __name__ == "__main__":
    unittest.main()
