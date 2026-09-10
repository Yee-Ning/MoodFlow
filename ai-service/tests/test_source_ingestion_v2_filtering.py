import unittest

from app.rag.source_ingestion.dedup import CandidateDeduplicator
from app.rag.source_ingestion.models import CandidateSource
from app.rag.source_ingestion.quality_filter import AutoSourceQualityFilter
from app.rag.source_ingestion.v2_models import DiscoveredCandidate


def _candidate(
    source_id: str,
    *,
    title: str = "周杰伦《叶惠美》专辑制作访谈",
    url: str | None = None,
    publisher: str = "Example Music",
    source_type: str = "interview",
    file_type: str = "html",
    artist: str = "周杰伦",
    topics: list[str] | None = None,
    source_tier: int = 2,
    source_quality: str = "high",
    canonical_url: str | None = None,
    external_id: str | None = None,
) -> DiscoveredCandidate:
    return DiscoveredCandidate(
        source_id=source_id,
        title=title,
        url=url or f"https://example.test/music/{source_id}",
        publisher=publisher,
        author=None,
        published_at=None,
        source_type=source_type,
        file_type=file_type,
        artist=artist,
        album="叶惠美",
        topics=topics or ["制作过程", "歌手/制作人/作词人采访"],
        source_tier=source_tier,
        source_quality=source_quality,
        reason="A traceable interview about Jay Chou's album production.",
        discovery_query="周杰伦 叶惠美 制作 采访",
        replacement_group="yehuimei-production",
        priority=1,
        replaces_source_id=None,
        canonical_url=canonical_url,
        external_id=external_id,
    )


class AutoSourceQualityFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.filter = AutoSourceQualityFilter()

    def test_relevant_tier_one_to_three_sources_are_accepted_without_approval(self) -> None:
        for tier in (1, 2, 3):
            with self.subTest(tier=tier):
                decision = self.filter.evaluate(
                    _candidate(f"candidate-{tier}", source_tier=tier)
                )
                self.assertTrue(decision.accepted)
                self.assertEqual(decision.reasons, [])

    def test_filter_fails_closed_for_low_quality_irrelevant_or_non_jay_sources(self) -> None:
        cases = {
            "low-quality": _candidate("low-quality", source_quality="low"),
            "tier-four": _candidate("tier-four", source_tier=4),
            "wrong-artist": _candidate("wrong-artist", artist="其他歌手"),
            "wrong-topic": _candidate(
                "wrong-topic", topics=["演唱会票务"], title="演唱会售票"
            ),
        }

        for expected_reason, candidate in cases.items():
            with self.subTest(expected_reason=expected_reason):
                decision = self.filter.evaluate(candidate)
                self.assertFalse(decision.accepted)
                self.assertIn(expected_reason, decision.reasons)

    def test_filter_rejects_search_login_ad_forum_and_ai_content_urls(self) -> None:
        cases = {
            "search-page": "https://example.test/search?q=jay+chou",
            "login-page": "https://example.test/login?next=/music/article",
            "advertising-page": "https://example.test/ads/jay-chou-campaign",
            "forum-source": "https://reddit.com/r/music/comments/jay-chou",
            "ai-content-source": "https://writesonic.com/blog/jay-chou-style",
        }

        for expected_reason, url in cases.items():
            with self.subTest(expected_reason=expected_reason):
                decision = self.filter.evaluate(
                    _candidate(expected_reason, url=url)
                )
                self.assertFalse(decision.accepted)
                self.assertIn(expected_reason, decision.reasons)


class CandidateDeduplicatorTests(unittest.TestCase):
    def test_normalized_url_removes_tracking_fragment_and_normalizes_host_path(self) -> None:
        existing = _candidate(
            "existing",
            url="https://WWW.Example.test/music/article/?id=7&utm_source=newsletter#bio",
        )
        candidate = _candidate(
            "new-source",
            url="https://example.test/music/article?id=7&utm_medium=email",
            title="Different title",
        )

        decision = CandidateDeduplicator([existing]).check(candidate)

        self.assertTrue(decision.is_duplicate)
        self.assertEqual(decision.reason, "normalized-url")
        self.assertEqual(decision.duplicate_of, "existing")

    def test_declared_canonical_url_detects_duplicate(self) -> None:
        existing = _candidate("existing", url="https://example.test/canonical")
        candidate = _candidate(
            "new-source",
            url="https://mirror.test/story",
            title="Different title",
            canonical_url="https://example.test/canonical/",
        )

        decision = CandidateDeduplicator([existing]).check(candidate)

        self.assertEqual(decision.reason, "canonical-url")

    def test_normalized_publisher_title_detects_duplicate(self) -> None:
        existing = _candidate(
            "existing", title="周杰伦 七里香 专访", publisher="Music Weekly"
        )
        candidate = _candidate(
            "new-source",
            title="  周杰伦   七里香 专访  ",
            publisher="music weekly",
        )

        decision = CandidateDeduplicator([existing]).check(candidate)

        self.assertEqual(decision.reason, "publisher-title")

    def test_known_external_id_detects_duplicate(self) -> None:
        existing = _candidate("existing", external_id="wikidata:Q595532")
        candidate = _candidate(
            "new-source", title="Different title", external_id="WIKIDATA:q595532"
        )

        decision = CandidateDeduplicator([existing]).check(candidate)

        self.assertEqual(decision.reason, "external-id")

    def test_distinct_candidate_is_registered_for_later_checks(self) -> None:
        deduplicator = CandidateDeduplicator([])
        first = _candidate("first")
        second = _candidate(
            "second", title="Different title", url="https://example.test/music/first"
        )

        self.assertFalse(deduplicator.check(first).is_duplicate)
        deduplicator.register(first)
        self.assertEqual(deduplicator.check(second).reason, "normalized-url")

    def test_existing_v1_candidate_can_seed_deduplication(self) -> None:
        discovered = _candidate("existing")
        existing = CandidateSource.model_validate(
            discovered.model_dump(include=set(CandidateSource.model_fields))
        )

        decision = CandidateDeduplicator([existing]).check(
            _candidate(
                "new-source",
                title="Different title",
                url="https://example.test/music/existing",
            )
        )

        self.assertEqual(decision.duplicate_of, "existing")


if __name__ == "__main__":
    unittest.main()
