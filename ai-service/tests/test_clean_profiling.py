import json
import tempfile
import unittest
from pathlib import Path

from app.rag.clean.config import ProfilingConfig
from app.rag.clean.corpus import NormalizedCorpusReader
from app.rag.clean.errors import NormalizedCorpusError
from app.rag.clean.profiling import NormalizedCorpusProfiler, write_profile_report
from tests.clean_helpers import normalized_document, write_normalized_corpus


class NormalizedCorpusReaderTests(unittest.TestCase):
    def test_reads_existing_source_dimensions_without_inventing_document_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_normalized_corpus(
                root,
                [
                    normalized_document(
                        "adele-news",
                        "Adele released 30 in 2021.\n",
                        title="Adele release",
                        publisher="Music Daily",
                        source_type="official_news",
                    )
                ],
            )

            loaded = NormalizedCorpusReader(root).load_all()

            self.assertEqual(loaded[0].source, "Music Daily")
            self.assertEqual(loaded[0].source_type, "official_news")
            self.assertIsNone(loaded[0].document_type)

    def test_rejects_manifest_path_outside_normalized_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            normalized_root = root / "data" / "normalized"
            normalized_root.mkdir(parents=True)
            (normalized_root / "normalized_corpus_manifest.jsonl").write_text(
                json.dumps(
                    {
                        "source_id": "unsafe",
                        "document_id": "doc-unsafe",
                        "normalized_path": "outside/document.json",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(NormalizedCorpusError):
                NormalizedCorpusReader(root).load_all()


class NormalizedCorpusProfilerTests(unittest.TestCase):
    def test_profiles_distribution_lengths_duplicates_and_templates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = "首页\n\n歌手：王菲\n\n发行：2001\n\n返回顶部\n"
            documents = [
                normalized_document(
                    "faye-wiki",
                    shared,
                    title="王菲",
                    publisher="Example Wiki",
                    source_type="encyclopedia",
                    document_type="artist",
                ),
                normalized_document(
                    "miles-wiki",
                    shared,
                    title="Miles Davis",
                    publisher="Example Wiki",
                    source_type="encyclopedia",
                    document_type="artist",
                ),
                normalized_document(
                    "taylor-news",
                    "首页\n\n歌曲：Anti-Hero\n\n排名：1\n\n返回顶部\n",
                    title="Chart report",
                    publisher="Music Daily",
                    source_type="media_article",
                    document_type="chart",
                ),
            ]
            write_normalized_corpus(root, documents)
            loaded = NormalizedCorpusReader(root).load_all()
            config = ProfilingConfig(
                frequent_line_min_documents=2,
                frequent_short_text_min_documents=2,
                repeated_sequence_min_documents=2,
                short_document_chars=100,
            )

            report = NormalizedCorpusProfiler(config).profile(loaded)

            self.assertEqual(report["document_count"], 3)
            self.assertEqual(report["source_distribution"]["Example Wiki"], 2)
            self.assertEqual(report["document_type_distribution"]["artist"], 2)
            self.assertEqual(len(report["exact_duplicate_groups"]), 1)
            frequent_lines = {
                row["text"]: row["document_count"]
                for row in report["frequent_lines"]
            }
            self.assertEqual(frequent_lines["首页"], 3)
            self.assertEqual(frequent_lines["发行：2001"], 2)
            self.assertIn("Frequency is a discovery signal", report["interpretation_note"])
            self.assertEqual(report["text_length"]["min"], min(map(len, [shared, shared, documents[2].markdown])))

            output = root / "data" / "clean" / "reports" / "profile.json"
            write_profile_report(report, output)
            persisted = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(persisted["document_count"], 3)


if __name__ == "__main__":
    unittest.main()
