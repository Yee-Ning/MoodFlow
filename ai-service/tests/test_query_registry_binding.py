import pytest
from hashlib import sha256
from pathlib import Path
import shutil

import yaml

from app.rag.query_understanding.service import QueryUnderstandingService
from app.rag.query_understanding.models import IndexProfile
from app.rag.query_understanding.registry_binding import load_bound_registry, registry_file_hashes


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = Path("app/rag/knowledge/entity_linking/registry")
SNAPSHOT_REGISTRY = Path("config_snapshot/entity_registry")


def bound_registry_hashes(version="v3", root=ROOT):
    result = {}
    selected = REGISTRY / (version + ".yaml")
    while True:
        content = (root / selected).read_bytes()
        result[selected.as_posix()] = "sha256:" + sha256(content).hexdigest()
        extends = yaml.safe_load(content).get("extends")
        if extends is None:
            return result
        selected = REGISTRY / extends


def copy_bound_registry(root, version="v3"):
    hashes = bound_registry_hashes(version)
    for relative in hashes:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return hashes


def profile(version):
    return IndexProfile(collection_name="independent", source_release_id="release",
                        index_schema_version="v2", projection_version="v4", registry_version=version,
                        registry_file_hashes=bound_registry_hashes(version))


def copy_registry_snapshot(root, directory=SNAPSHOT_REGISTRY):
    hashes = {}
    for relative, digest in bound_registry_hashes().items():
        target = directory / Path(relative).name
        (root / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, root / target)
        hashes[target.as_posix()] = digest
    return profile("v3").model_copy(update={"registry_file_hashes": hashes})


@pytest.fixture
def snapshot_profile(tmp_path):
    return tmp_path, copy_registry_snapshot(tmp_path)


@pytest.mark.parametrize("directory", [SNAPSHOT_REGISTRY, Path("releases/frozen") / SNAPSHOT_REGISTRY])
def test_release_snapshot_registry_loads_from_declared_relative_paths(tmp_path, monkeypatch, directory):
    selected = copy_registry_snapshot(tmp_path, directory)
    assert not (tmp_path / REGISTRY).exists()

    def unchecked_reload(*args, **kwargs):
        raise AssertionError("must load the verified snapshots")

    monkeypatch.setattr("app.rag.knowledge.entity_linking.registry.EntityRegistry.load", unchecked_reload)
    service = QueryUnderstandingService(index_profile=selected, project_root=tmp_path)
    assert service.entity_resolver.registry.version == "v3"
    assert service.entity_resolver.registry.get("artist:jay_chou") is not None
    assert service.entity_resolver.registry.get("album:greatest_works_of_art") is not None
    assert service.context_resolver.registry is service.entity_resolver.registry


@pytest.mark.parametrize("filename", ["v3.yaml", "v2.yaml"])
def test_snapshot_registry_and_extends_still_require_exact_hashes(snapshot_profile, filename):
    root, selected = snapshot_profile
    target = root / SNAPSHOT_REGISTRY / filename
    target.write_bytes(target.read_bytes() + b"\n# same version, changed snapshot bytes\n")
    with pytest.raises(ValueError, match="registry_hash_mismatch"):
        load_bound_registry(selected, root)


@pytest.mark.parametrize("relative", [
    "../v3.yaml", "config_snapshot/../../../v3.yaml", "/snapshots/v3.yaml",
    "C:/snapshots/v3.yaml", "C:snapshots/v3.yaml", r"\snapshots\v3.yaml",
    r"\\server\share\v3.yaml",
])
def test_snapshot_registry_rejects_anchored_or_escaping_paths(snapshot_profile, relative):
    root, selected = snapshot_profile
    with pytest.raises(ValueError, match="registry_path_outside_project"):
        load_bound_registry(selected.model_copy(update={
            "registry_file_hashes": {relative: "sha256:" + "0" * 64},
        }), root)


def test_absolute_registry_path_inside_project_is_rejected(snapshot_profile):
    root, selected = snapshot_profile
    hashes = {str((root / relative).resolve()): digest
              for relative, digest in selected.registry_file_hashes.items()}
    with pytest.raises(ValueError, match="registry_path_outside_project"):
        load_bound_registry(selected.model_copy(update={"registry_file_hashes": hashes}), root)


def rewrite_snapshot_extends(root, selected, extends):
    relative = SNAPSHOT_REGISTRY / "v3.yaml"
    target = root / relative
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    payload["extends"] = extends
    target.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    hashes = dict(selected.registry_file_hashes)
    hashes[relative.as_posix()] = "sha256:" + sha256(target.read_bytes()).hexdigest()
    return selected.model_copy(update={"registry_file_hashes": hashes})


@pytest.mark.parametrize("extends", ["../../../v2.yaml", "/snapshots/v2.yaml", "C:/snapshots/v2.yaml"])
def test_snapshot_extends_cannot_use_absolute_or_escaping_paths(snapshot_profile, extends):
    root, selected = snapshot_profile
    selected = rewrite_snapshot_extends(root, selected, extends)
    with pytest.raises(ValueError, match="registry_path_outside_project"):
        load_bound_registry(selected, root)


def test_snapshot_extends_absolute_path_inside_project_is_rejected(snapshot_profile):
    root, selected = snapshot_profile
    selected = rewrite_snapshot_extends(root, selected, str((root / SNAPSHOT_REGISTRY / "v2.yaml").resolve()))
    with pytest.raises(ValueError, match="registry_path_outside_project"):
        load_bound_registry(selected, root)


def test_snapshot_extends_can_reference_a_declared_base_elsewhere_inside_project(snapshot_profile):
    root, selected = snapshot_profile
    relative = Path("shared_registry/v2.yaml")
    (root / relative).parent.mkdir()
    shutil.copyfile(root / SNAPSHOT_REGISTRY / "v2.yaml", root / relative)
    hashes = dict(selected.registry_file_hashes)
    hashes[relative.as_posix()] = hashes.pop((SNAPSHOT_REGISTRY / "v2.yaml").as_posix())
    selected = selected.model_copy(update={"registry_file_hashes": hashes})
    selected = rewrite_snapshot_extends(root, selected, "../../shared_registry/v2.yaml")
    assert load_bound_registry(selected, root).get("artist:jay_chou") is not None


def test_snapshot_entry_must_be_unique_and_declared(snapshot_profile):
    root, selected = snapshot_profile
    hashes = dict(selected.registry_file_hashes)
    digest = hashes.pop((SNAPSHOT_REGISTRY / "v3.yaml").as_posix())
    with pytest.raises(ValueError, match="registry_dependency_missing"):
        load_bound_registry(selected.model_copy(update={"registry_file_hashes": hashes}), root)
    hashes.update(selected.registry_file_hashes)
    hashes["other_snapshot/v3.yaml"] = digest
    with pytest.raises(ValueError, match="registry_hashes_invalid"):
        load_bound_registry(selected.model_copy(update={"registry_file_hashes": hashes}), root)


def test_snapshot_symlink_cannot_escape_project(snapshot_profile):
    root, selected = snapshot_profile
    outside = root.parent / (root.name + "-outside")
    outside.mkdir()
    shutil.copyfile(root / SNAPSHOT_REGISTRY / "v3.yaml", outside / "v3.yaml")
    link = root / "linked_registry"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc.__class__.__name__}")
    selected = selected.model_copy(update={"registry_file_hashes": {
        "linked_registry/v3.yaml": selected.registry_file_hashes[(SNAPSHOT_REGISTRY / "v3.yaml").as_posix()],
    }})
    with pytest.raises(ValueError, match="registry_path_outside_project"):
        load_bound_registry(selected, root)


def test_default_resolvers_use_the_bound_index_registry():
    service = QueryUnderstandingService(index_profile=profile("v5"))
    assert service.entity_resolver.registry.version == "v5"
    assert service.context_resolver.registry.version == "v5"
    assert service.entity_resolver.registry.get("artist:nirvana") is not None
    assert QueryUnderstandingService().entity_resolver.registry.version == "v3"


def test_unavailable_bound_registry_fails_instead_of_using_default():
    selected = profile("v5").model_copy(update={
        "registry_version": "v999999", "registry_file_hashes": {
            (REGISTRY / "v999999.yaml").as_posix(): "sha256:" + "0" * 64,
        },
    })
    with pytest.raises(ValueError, match="registry_file_unavailable"):
        QueryUnderstandingService(index_profile=selected)


@pytest.fixture
def bound_profile(tmp_path):
    copy_bound_registry(tmp_path)
    return tmp_path, profile("v3")


def test_bound_service_loads_matching_registry_and_extends(bound_profile, monkeypatch):
    root, selected = bound_profile
    def unchecked_reload(*args, **kwargs):
        raise AssertionError("must load the verified snapshots")
    monkeypatch.setattr("app.rag.knowledge.entity_linking.registry.EntityRegistry.load", unchecked_reload)
    service = QueryUnderstandingService(index_profile=selected, project_root=root)
    assert service.entity_resolver.registry.get("artist:jay_chou") is not None
    assert service.context_resolver.registry.version == "v3"


@pytest.mark.parametrize("filename", ["v3.yaml", "v2.yaml"])
def test_same_version_registry_or_base_hash_drift_fails(bound_profile, filename):
    root, selected = bound_profile
    target = root / REGISTRY / filename
    target.write_bytes(target.read_bytes() + b"\n# same version, changed bytes\n")
    with pytest.raises(ValueError, match="registry_hash_mismatch"):
        QueryUnderstandingService(index_profile=selected, project_root=root)


def test_missing_dependency_or_hash_is_rejected(bound_profile):
    root, selected = bound_profile
    with pytest.raises(ValueError, match="registry_hashes_missing"):
        load_bound_registry(selected.model_copy(update={"registry_file_hashes": {}}), root)
    hashes = dict(selected.registry_file_hashes)
    hashes.pop((REGISTRY / "v2.yaml").as_posix())
    with pytest.raises(ValueError, match="registry_dependency_missing"):
        load_bound_registry(selected.model_copy(update={"registry_file_hashes": hashes}), root)


def test_declared_registry_paths_cannot_escape_project(bound_profile):
    root, selected = bound_profile
    with pytest.raises(ValueError, match="registry_path_outside_project"):
        load_bound_registry(selected.model_copy(update={
            "registry_file_hashes": {"../v3.yaml": "sha256:" + "0" * 64},
        }), root)


@pytest.mark.parametrize("rows", [None, [], [{"path": "v3.yaml", "sha256": "bad"}],
                                  [{"path": "v3.yaml", "sha256": "sha256:" + "0" * 64}] * 2])
def test_release_hash_list_is_required_and_unambiguous(rows):
    with pytest.raises(ValueError, match="registry_hashes_"):
        registry_file_hashes({"source": {"entity_registry_files": rows}})
