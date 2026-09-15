import unittest

from app.agent.foundation.subgraphs.worldline.grounding import (
    WorldLineGroundingError,
    WorldLineGroundingValidator,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EvidenceSource,
    EvidenceType,
    NarrativeOutput,
)


def source() -> EvidenceSource:
    return EvidenceSource(
        id="knowledge:parent-1",
        source_type=EvidenceType.KNOWLEDGE,
        title="晴天资料",
        source="测试知识库",
        excerpt="《晴天》收录于《叶惠美》。",
    )


class WorldLineGroundingTests(unittest.TestCase):
    def test_accepts_citations_in_first_use_order(self) -> None:
        output = NarrativeOutput(
            narrative="《晴天》收录于《叶惠美》。[knowledge:parent-1]",
            citation_ids=["knowledge:parent-1"],
        )

        WorldLineGroundingValidator().validate(output, [source()])

    def test_rejects_unknown_and_unanchored_citations(self) -> None:
        with self.assertRaisesRegex(WorldLineGroundingError, "unknown"):
            WorldLineGroundingValidator().validate(
                NarrativeOutput(
                    narrative="这是一段有引用的事实。[knowledge:missing]",
                    citation_ids=["knowledge:missing"],
                ),
                [source()],
            )
        with self.assertRaisesRegex(WorldLineGroundingError, "match inline"):
            WorldLineGroundingValidator().validate(
                NarrativeOutput(
                    narrative="这是一段没有内联引用的事实。",
                    citation_ids=["knowledge:parent-1"],
                ),
                [source()],
            )

    def test_rejects_substantive_paragraph_without_anchor(self) -> None:
        output = NarrativeOutput(
            narrative=(
                "第一段提供了充分的背景事实但没有引用。\n"
                "第二段有来源。[knowledge:parent-1]"
            ),
            citation_ids=["knowledge:parent-1"],
        )

        with self.assertRaisesRegex(WorldLineGroundingError, "each substantive"):
            WorldLineGroundingValidator().validate(output, [source()])


if __name__ == "__main__":
    unittest.main()
