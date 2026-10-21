"""Load exactly the registry bytes declared by an index's knowledge release."""

from hashlib import sha256
from pathlib import Path, PureWindowsPath
import re

import yaml

from app.rag.knowledge.entity_linking.registry import EntityRegistry
from app.rag.knowledge.entity_linking.models import EntityRegistryDocument
from .models import IndexProfile


def registry_file_hashes(release: dict) -> dict[str, str]:
    source = release.get("source")
    rows = source.get("entity_registry_files") if isinstance(source, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("query_index_registry_hashes_missing")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("query_index_registry_hashes_invalid")
        path, digest = row.get("path"), row.get("sha256")
        if (not isinstance(path, str) or not path or path in result
                or not isinstance(digest, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None):
            raise ValueError("query_index_registry_hashes_invalid")
        result[path] = digest
    return result


def _relative_registry_path(root: Path, base: Path, relative: str) -> Path:
    candidate = Path(relative)
    # Reject rooted and drive-relative Windows paths on every platform as well.
    if candidate.anchor or PureWindowsPath(relative).anchor:
        raise ValueError("query_index_registry_path_outside_project")
    path = (base / candidate).resolve()
    if not path.is_relative_to(root) or path.suffix != ".yaml":
        raise ValueError("query_index_registry_path_outside_project")
    return path


def load_bound_registry(profile: IndexProfile, project_root: Path | None = None) -> EntityRegistry:
    root = (project_root or Path(__file__).resolve().parents[3]).resolve()
    if not profile.registry_file_hashes:
        raise ValueError("query_index_registry_hashes_missing")
    declared = {}
    for relative, digest in profile.registry_file_hashes.items():
        path = _relative_registry_path(root, root, relative)
        if path in declared or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise ValueError("query_index_registry_hashes_invalid")
        declared[path] = digest

    entries = [path for path in declared if path.name == profile.registry_version + ".yaml"]
    if not entries:
        raise ValueError("query_index_registry_dependency_missing")
    if len(entries) != 1:
        raise ValueError("query_index_registry_hashes_invalid")
    verified = {}
    selected = entries[0]
    current = selected
    while True:
        if current in verified:
            raise ValueError("query_index_registry_extends_cycle")
        if current not in declared:
            raise ValueError("query_index_registry_dependency_missing")
        try:
            content = current.read_bytes()
        except OSError as exc:
            raise ValueError("query_index_registry_file_unavailable") from exc
        if "sha256:" + sha256(content).hexdigest() != declared[current]:
            raise ValueError("query_index_registry_hash_mismatch")
        try:
            payload = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError("query_index_registry_invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("query_index_registry_invalid")
        verified[current] = payload
        extends = payload.get("extends")
        if extends is None:
            break
        if not isinstance(extends, str) or not extends:
            raise ValueError("query_index_registry_extends_invalid")
        current = _relative_registry_path(root, current.parent, extends)
    if set(verified) != set(declared):
        raise ValueError("query_index_registry_dependencies_mismatch")
    # Construct from the verified snapshots; never reopen unchecked YAML bytes.
    payload = dict(verified[selected])
    extends = payload.pop("extends", None)
    if extends is not None:
        base = verified[_relative_registry_path(root, selected.parent, extends)]
        own = payload.get("entities", [])
        if not isinstance(base.get("entities"), list) or not isinstance(own, list):
            raise ValueError("query_index_registry_invalid")
        payload["entities"] = [*base["entities"], *own]
    registry = EntityRegistry(EntityRegistryDocument.model_validate(payload))
    if registry.version != profile.registry_version:
        raise ValueError("query_index_registry_version_mismatch")
    return registry
