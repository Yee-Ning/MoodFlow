"""Input-only overlays: unchanged reviewed Gold, source snapshots and claims."""

from copy import deepcopy
import json
from pathlib import Path

from app.agent.foundation.subgraphs.worldline.source_context import RelationSourceContext
from evals.semantic_relations_models import ReplayInput, file_hash, load_suite, read_jsonl


def load_context_inputs(directory: Path, manifest: dict):
    required = {"inputs.jsonl", "context_sources.jsonl", "schema.json", "capture_audit.json", ".gitattributes"}
    if set(manifest["files"]) != required:
        raise ValueError("context_freeze_files_mismatch")
    for name, digest in manifest["files"].items():
        if file_hash(directory / name) != digest:
            raise ValueError(f"context_freeze_hash_mismatch:{name}")
    base = (directory / manifest["base_directory"]).resolve()
    if base == directory.resolve():
        raise ValueError("context_base_cycle")
    if file_hash(base / "manifest.json") != manifest["base_manifest_sha256"]:
        raise ValueError("context_base_manifest_hash_mismatch")
    base_manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    if base_manifest.get("kind") == "relation_context_input":
        raise ValueError("context_overlay_base_must_be_gold_suite")
    if (manifest["base_files"] != base_manifest["files"]
            or manifest["base_review_files"] != base_manifest["review_files"]):
        raise ValueError("context_base_lineage_mismatch")
    for name, digest in manifest["base_review_files"].items():
        path = (base / name).resolve()
        if not path.is_relative_to(base) or file_hash(path) != digest:
            raise ValueError("context_base_review_hash_mismatch")
    if json.loads((directory / "schema.json").read_text(encoding="utf-8")) != RelationSourceContext.model_json_schema():
        raise ValueError("context_schema_mismatch")
    cases, original_inputs, sources, _ = load_suite(base)
    captures = read_jsonl(directory / "context_sources.jsonl", RelationSourceContext)
    capture_map = {item.context_id: item for item in captures}
    if len(capture_map) != len(captures):
        raise ValueError("context_duplicate_capture")
    replays = read_jsonl(directory / "inputs.jsonl", ReplayInput)
    input_map = {item.case_id: item for item in replays}
    if len(input_map) != len(replays) or set(input_map) != set(original_inputs):
        raise ValueError("context_input_ids_mismatch")
    used = []
    for case_id, replay in input_map.items():
        stripped = deepcopy(replay.model_dump(mode="json"))
        for doc, raw in zip(replay.documents, stripped["documents"]):
            payload = raw["metadata"].pop("relation_source_context", None)
            if payload is None:
                continue
            context = RelationSourceContext.model_validate(payload)
            if capture_map.get(context.context_id) != context:
                raise ValueError("context_input_capture_mismatch")
            claims = doc.metadata.get("supported_claims", [])
            claim = next((c for c in claims if c["claim_id"] == context.claim_id), None)
            if claim is None or not context.bound_to(doc, claim["claim_id"], claim["evidence_span"]):
                raise ValueError("context_input_binding_mismatch")
            if sources[context.source_id].origin != "real_source":
                raise ValueError("context_real_source_required")
            used.append(context.context_id)
        if stripped != original_inputs[case_id].model_dump(mode="json"):
            raise ValueError("context_overlay_changed_base_input")
    if sorted(used) != sorted(capture_map):
        raise ValueError("context_unused_or_duplicate_capture")
    return cases, input_map, sources, manifest
