"""Atomic build manifest and failure report persistence."""

from __future__ import annotations

from pathlib import Path

from app.rag.clean.io import atomic_write_json, atomic_write_jsonl

from .models import IndexBuildManifest, IndexFailure


class IndexBuildWriter:
    def __init__(self, project_root: Path, output_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.output_root = output_root.resolve()
        if not self.output_root.is_relative_to(self.project_root):
            raise ValueError("index_output_path_outside_project")

    @property
    def manifest_path(self) -> Path:
        return self.output_root / "index_build_manifest.json"

    @property
    def failures_path(self) -> Path:
        return self.output_root / "index_failures.jsonl"

    def write(
        self, manifest: IndexBuildManifest, failures: list[IndexFailure]
    ) -> tuple[Path, Path]:
        atomic_write_json(self.manifest_path, manifest.model_dump(mode="json"))
        atomic_write_jsonl(
            self.failures_path,
            (failure.model_dump(mode="json") for failure in failures),
        )
        return self.manifest_path, self.failures_path
