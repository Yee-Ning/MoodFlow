import asyncio
import tempfile
import unittest
from pathlib import Path

from app.rag.clean.config import load_clean_config
from app.rag.clean.corpus import NormalizedCorpusReader
from app.rag.clean.discovery import (
    NOISE_DISCOVERY_PROMPT,
    CandidateNoisePattern,
    NoiseDiscoveryBatchResult,
    NoiseDiscoveryService,
)
from app.rag.clean.sampling import (
    StratifiedSampler,
    load_sampling_manifest,
    write_sampling_manifest,
)
from tests.clean_helpers import normalized_document, write_normalized_corpus


class FakeDiscoveryRuntime:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, object]]] = []

    async def analyze(
        self,
        documents: list[dict[str, object]],
        *,
        max_candidates: int,
    ) -> NoiseDiscoveryBatchResult:
        self.calls.append(documents)
        return NoiseDiscoveryBatchResult(
            candidates=[
                CandidateNoisePattern(
                    pattern_or_example="返回顶部",
                    noise_type="navigation_ui",
                    recommended_action="DROP_LINE",
                    applicable_source=[str(documents[0]["source"])],
                    applicable_document_type=[],
                    reason="Standalone page navigation control.",
                    confidence=0.98,
                    false_positive_risk="low when exact and source-scoped",
                    uncertain=False,
                    evidence_document_ids=[str(documents[0]["document_id"])],
                )
            ],
            batch_notes=["Dates and artist names were retained as facts."],
        )


class StratifiedSamplingTests(unittest.TestCase):
    def test_sample_is_reproducible_and_degrades_to_source_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _ = load_clean_config()
            documents = [
                normalized_document(
                    "artist-short",
                    "歌手：陈奕迅\n",
                    title="陈奕迅",
                    publisher="Label A",
                    source_type="official_profile",
                ),
                normalized_document(
                    "album-medium",
                    "专辑：黑色柳丁\n" * 200,
                    title="黑色柳丁",
                    publisher="Label A",
                    source_type="record_label",
                ),
                normalized_document(
                    "chart-long",
                    "Rank 1: Kind of Blue\n" * 600,
                    title="Jazz chart",
                    publisher="Chart Source",
                    source_type="media_article",
                ),
                normalized_document(
                    "news-short",
                    "AURORA released a single in 2024.\n",
                    title="Release news",
                    publisher="Music Daily",
                    source_type="official_news",
                ),
            ]
            write_normalized_corpus(root, documents)
            loaded = NormalizedCorpusReader(root).load_all()
            sampler = StratifiedSampler(
                config.sampling.model_copy(update={"sample_size": 3})
            )

            first = sampler.sample(loaded)
            second = sampler.sample(list(reversed(loaded)))

            self.assertEqual(first, second)
            self.assertEqual(first.actual_size, 3)
            self.assertEqual(
                first.dimensions_used,
                ["source", "source_type", "length_bucket"],
            )
            self.assertIn("document_type", first.degraded_dimensions)
            self.assertTrue(all(row.document_type is None for row in first.documents))
            output = root / "sampling_manifest.json"
            write_sampling_manifest(first, output)
            self.assertEqual(load_sampling_manifest(output), first)

    def test_uses_real_document_type_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _ = load_clean_config()
            write_normalized_corpus(
                root,
                [
                    normalized_document(
                        "bjork-artist",
                        "Artist: Björk\n",
                        title="Björk",
                        document_type="artist",
                    ),
                    normalized_document(
                        "bjork-album",
                        "Album: Homogenic\n",
                        title="Homogenic",
                        document_type="album",
                    ),
                ],
            )

            manifest = StratifiedSampler(config.sampling).sample(
                NormalizedCorpusReader(root).load_all()
            )

            self.assertEqual(
                manifest.dimensions_used,
                ["source", "document_type", "length_bucket"],
            )
            self.assertEqual(manifest.degraded_dimensions, {})


class NoiseDiscoveryTests(unittest.TestCase):
    def test_produces_unreviewed_candidates_without_modifying_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _ = load_clean_config()
            write_normalized_corpus(
                root,
                [
                    normalized_document(
                        "radiohead-review",
                        "返回顶部\n\nArtist: Radiohead\n\n1997\n",
                        title="OK Computer review",
                        publisher="Example Review",
                        document_type="review",
                    )
                ],
            )
            loaded = NormalizedCorpusReader(root).load_all()
            manifest = StratifiedSampler(config.sampling).sample(loaded)
            runtime = FakeDiscoveryRuntime()
            service = NoiseDiscoveryService(
                config.noise_discovery,
                clean_version=config.clean_version,
                max_text_chars_per_document=config.sampling.max_text_chars_per_document,
                runtime=runtime,
            )

            report = asyncio.run(service.discover(loaded, manifest))

            self.assertEqual(report["candidate_count"], 1)
            self.assertEqual(report["candidate_rules"][0]["review_status"], "unreviewed")
            self.assertFalse(report["registry_modified"])
            self.assertTrue(report["human_review_required"])
            self.assertEqual(runtime.calls[0][0]["document_type"], "review")

    def test_prompt_contains_domain_fact_and_uncertainty_guards(self) -> None:
        self.assertIn("High frequency does not mean noise", NOISE_DISCOVERY_PROMPT)
        self.assertIn("Dates, numbers, ranks", NOISE_DISCOVERY_PROMPT)
        self.assertIn("Mark uncertain=true", NOISE_DISCOVERY_PROMPT)
        self.assertIn("Never create an entity-specific", NOISE_DISCOVERY_PROMPT)
        self.assertIn("do not infer one from source_type", NOISE_DISCOVERY_PROMPT)


if __name__ == "__main__":
    unittest.main()
