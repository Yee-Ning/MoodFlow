import json
import tempfile
import unittest
from pathlib import Path

from app.rag.clean.corpus import NormalizedDocumentContext
from app.rag.clean.table_inspection import (
    LAYOUT_CANDIDATE,
    SEMANTIC_CANDIDATE,
    NormalizedTableInspector,
    write_table_inspection_report,
)
from app.rag.parse_normalize.models import (
    BlockType,
    DocumentBlock,
    NormalizedDocument,
)
from tests.clean_helpers import normalized_document


class NormalizedTableInspectorTests(unittest.TestCase):
    def _context(
        self, source_id: str, rows: list[list[str]], *, has_header: bool
    ) -> NormalizedDocumentContext:
        table = DocumentBlock(
            block_id=f"{source_id}-b000000",
            block_type=BlockType.TABLE,
            text="\n".join(" | ".join(row) for row in rows),
            order_index=0,
            metadata={
                "rows": rows,
                "has_header": has_header,
                "table_kind_hint": (
                    "semantic_candidate" if has_header else "uncertain"
                ),
            },
        )
        base = normalized_document(
            source_id,
            "placeholder",
            title="Table fixture",
            publisher="Example Source",
            document_type="album",
        )
        document: NormalizedDocument = base.model_copy(
            update={"blocks": [table], "tables": [], "markdown": table.text}
        )
        return NormalizedDocumentContext(
            document=document,
            normalized_path=Path(f"data/normalized/{source_id}/document.json"),
            relative_normalized_path=f"data/normalized/{source_id}/document.json",
            source="Example Source",
            source_type="fixture",
            document_type="album",
            document_type_origin=None,
            document_type_sidecar_version=None,
            document_type_sidecar_checksum=None,
        )

    def test_explicit_header_is_semantic_candidate(self) -> None:
        report = NormalizedTableInspector().inspect(
            [
                self._context(
                    "semantic", [["Album", "Year"], ["Blue", "1971"]], has_header=True
                )
            ]
        )

        self.assertEqual(report["table_count"], 1)
        self.assertEqual(report["tables"][0]["classification"], SEMANTIC_CANDIDATE)
        self.assertFalse(report["policy"]["automatic_table_deletion"])

    def test_sparse_single_row_table_is_layout_candidate_only(self) -> None:
        report = NormalizedTableInspector().inspect(
            [self._context("layout", [["首页", "", "新闻", "", "邮箱"]], has_header=False)]
        )

        table = report["tables"][0]
        self.assertEqual(table["classification"], LAYOUT_CANDIDATE)
        self.assertIn("single_row_table", table["reasons"])
        self.assertEqual(report["review_document_count"], 1)

    def test_summary_and_manifest_are_written_separately(self) -> None:
        report = NormalizedTableInspector().inspect(
            [self._context("semantic", [["Song", "Rank"], ["Halo", "1"]], has_header=True)]
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "table_summary.json"

            paths = write_table_inspection_report(report, output)

            summary = json.loads(paths.summary.read_text(encoding="utf-8"))
            rows = paths.manifest.read_text(encoding="utf-8").splitlines()
            self.assertNotIn("tables", summary)
            self.assertEqual(summary["table_manifest"], "table_manifest.jsonl")
            self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
