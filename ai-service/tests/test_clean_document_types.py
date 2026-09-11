import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path

import yaml

from app.rag.clean.config import DEFAULT_CLEAN_CONFIG_PATH, load_clean_config
from app.rag.clean.corpus import NormalizedCorpusReader
from app.rag.clean.document_types import (
    DocumentTypeAssignment,
    DocumentTypeOrigin,
    DocumentTypeReviewStatus,
    DocumentTypeSidecar,
)
from app.rag.clean.errors import CleanConfigError, NormalizedCorpusError
from tests.clean_helpers import normalized_document, write_normalized_corpus


def assignment(
    source_id: str,
    document_type: str,
    *,
    status: DocumentTypeReviewStatus = DocumentTypeReviewStatus.APPROVED,
) -> DocumentTypeAssignment:
    return DocumentTypeAssignment(
        source_id=source_id,
        document_type=document_type,
        review_status=status,
        reason="The page purpose is explicit in its title and URL.",
        evidence=["title and URL identify the page type"],
        reviewed_by=(
            "test-reviewer"
            if status is DocumentTypeReviewStatus.APPROVED
            else None
        ),
        reviewed_at=date(2026, 9, 11)
        if status is DocumentTypeReviewStatus.APPROVED
        else None,
    )


def sidecar(*assignments: DocumentTypeAssignment) -> DocumentTypeSidecar:
    return DocumentTypeSidecar(
        sidecar_version="v0.2",
        assignment_policy="Only manually reviewed assignments are applied.",
        assignments=list(assignments),
    )


class DocumentTypeSidecarTests(unittest.TestCase):
    def test_v01_registry_remains_loadable_without_a_sidecar(self) -> None:
        config, _ = load_clean_config(
            DEFAULT_CLEAN_CONFIG_PATH.with_name("v0.1.yaml")
        )

        self.assertEqual(config.clean_version, "v0.1")
        self.assertIsNone(config.document_type_sidecar)
        self.assertIsNone(config.document_type_sidecar_checksum)

    def test_approved_sidecar_fills_missing_type_without_mutating_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_normalized_corpus(
                root,
                [
                    normalized_document(
                        "adele-album",
                        "Album: 25\nRelease: 2015\n",
                        title="25",
                        document_type=None,
                    )
                ],
            )
            normalized_path = root / "data/normalized/adele-album/document.json"
            before_hash = hashlib.sha256(normalized_path.read_bytes()).hexdigest()
            checksum = "sha256:" + "a" * 64

            context = NormalizedCorpusReader(
                root,
                document_type_sidecar=sidecar(
                    assignment("adele-album", "album")
                ),
                document_type_sidecar_checksum=checksum,
            ).load_all()[0]

            self.assertEqual(context.document_type, "album")
            self.assertEqual(context.document_type_origin, DocumentTypeOrigin.SIDECAR)
            self.assertEqual(context.document_type_sidecar_version, "v0.2")
            self.assertEqual(context.document_type_sidecar_checksum, checksum)
            self.assertEqual(
                hashlib.sha256(normalized_path.read_bytes()).hexdigest(), before_hash
            )

    def test_pending_assignment_is_not_applied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_normalized_corpus(
                root,
                [
                    normalized_document(
                        "uncertain-page",
                        "Music page\n",
                        title="Uncertain page",
                        document_type=None,
                    )
                ],
            )

            context = NormalizedCorpusReader(
                root,
                document_type_sidecar=sidecar(
                    assignment(
                        "uncertain-page",
                        "news",
                        status=DocumentTypeReviewStatus.PENDING,
                    )
                ),
            ).load_all()[0]

            self.assertIsNone(context.document_type)
            self.assertIsNone(context.document_type_origin)

    def test_normalized_type_wins_and_conflicting_approved_sidecar_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_normalized_corpus(
                root,
                [
                    normalized_document(
                        "prince-song",
                        "Song: Purple Rain\n",
                        title="Purple Rain",
                        document_type="song",
                    )
                ],
            )

            with self.assertRaisesRegex(
                NormalizedCorpusError, "document_type_sidecar_conflict:prince-song"
            ):
                NormalizedCorpusReader(
                    root,
                    document_type_sidecar=sidecar(
                        assignment("prince-song", "album")
                    ),
                    document_type_sidecar_checksum="sha256:" + "b" * 64,
                ).load_all()

    def test_config_fingerprint_includes_sidecar_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = yaml.safe_load(
                DEFAULT_CLEAN_CONFIG_PATH.read_text(encoding="utf-8")
            )
            payload["document_type_sidecar_path"] = "document-types.yaml"
            payload["known_document_types"] = ["album", "news"]
            config_path = root / "config.yaml"
            config_path.write_text(
                yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            sidecar_path = root / "document-types.yaml"
            first_payload = sidecar(
                assignment("adele-album", "album")
            ).model_dump(mode="json")
            sidecar_path.write_text(
                yaml.safe_dump(first_payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

            config, first_checksum = load_clean_config(config_path)
            first_sidecar_checksum = config.document_type_sidecar_checksum
            second_payload = sidecar(
                assignment("adele-album", "news")
            ).model_dump(mode="json")
            sidecar_path.write_text(
                yaml.safe_dump(second_payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            _, second_checksum = load_clean_config(config_path)

            self.assertNotEqual(first_checksum, second_checksum)
            self.assertNotEqual(
                first_sidecar_checksum,
                "sha256:" + hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
            )

    def test_config_rejects_sidecar_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = yaml.safe_load(
                DEFAULT_CLEAN_CONFIG_PATH.read_text(encoding="utf-8")
            )
            payload["document_type_sidecar_path"] = "../outside.yaml"
            config_path = root / "rules/config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

            with self.assertRaises(CleanConfigError):
                load_clean_config(config_path)


if __name__ == "__main__":
    unittest.main()
