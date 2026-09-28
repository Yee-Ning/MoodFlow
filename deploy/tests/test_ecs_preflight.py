import hashlib

import pytest

from deploy import ecs_preflight


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_preflight_accepts_verified_inputs_without_reading_secrets(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    (checkout / "netease-music-api").mkdir(parents=True)
    for name in ("app.js", "pnpm-lock.yaml"):
        (checkout / "netease-music-api" / name).write_text("fixture")
    artifacts = tmp_path / "artifacts"
    (artifacts / "wheelhouse").mkdir(parents=True)
    (artifacts / "wheelhouse" / ecs_preflight.TORCH_WHEEL).write_bytes(b"wheel-fixture")
    monkeypatch.setattr(ecs_preflight, "TORCH_SHA256", sha(b"wheel-fixture"))
    monkeypatch.setattr(ecs_preflight, "verify", lambda root: {
        "source_release_id": "release", "collection_name": "approved",
    })
    monkeypatch.setattr(ecs_preflight, "verify_model_cache", lambda root: {"revision": "pinned"})
    dump = tmp_path / "backup.dump"
    dump.write_bytes(b"dump-fixture")
    snapshot = tmp_path / "approved-1.snapshot"
    snapshot.write_bytes(b"snapshot-fixture")
    env_file = tmp_path / ".env"
    env_file.write_text("only-paths", encoding="utf-8")
    secrets = tuple(tmp_path / name for name in ("pg", "llm", "qdrant"))
    for path in secrets:
        path.write_text("secret-fixture", encoding="utf-8")
    result = ecs_preflight.check_inputs(checkout, artifacts, dump, sha(b"dump-fixture"),
                                        snapshot, sha(b"snapshot-fixture"), env_file, secrets)
    assert result == {"verified": True, "release": "release", "collection": "approved",
                      "model_revision": "pinned"}

    snapshot.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="sha256_mismatch:qdrant_snapshot"):
        ecs_preflight.check_inputs(checkout, artifacts, dump, sha(b"dump-fixture"),
                                   snapshot, sha(b"snapshot-fixture"), env_file, secrets)


@pytest.mark.parametrize("name", ["missing_env_file", "missing_or_empty_secret_file:llm_api_key",
                                 "netease_submodule_not_initialized", "qdrant_snapshot_collection_mismatch"])
def test_preflight_rejects_missing_or_wrong_inputs(tmp_path, monkeypatch, name):
    checkout = tmp_path / "checkout"
    (checkout / "netease-music-api").mkdir(parents=True)
    for file in ("app.js", "pnpm-lock.yaml"):
        (checkout / "netease-music-api" / file).write_text("fixture")
    artifacts = tmp_path / "artifacts"
    (artifacts / "wheelhouse").mkdir(parents=True)
    (artifacts / "wheelhouse" / ecs_preflight.TORCH_WHEEL).write_bytes(b"wheel")
    monkeypatch.setattr(ecs_preflight, "TORCH_SHA256", sha(b"wheel"))
    monkeypatch.setattr(ecs_preflight, "verify", lambda root: {
        "source_release_id": "release", "collection_name": "approved",
    })
    monkeypatch.setattr(ecs_preflight, "verify_model_cache", lambda root: {"revision": "pinned"})
    dump = tmp_path / "backup.dump"
    dump.write_bytes(b"dump")
    snapshot = tmp_path / "approved-1.snapshot"
    snapshot.write_bytes(b"snapshot")
    env_file = tmp_path / ".env"
    env_file.write_text("fixture")
    secrets = tuple(tmp_path / file for file in ("pg", "llm", "qdrant"))
    for path in secrets:
        path.write_text("fixture")
    if name == "missing_env_file":
        env_file.unlink()
    elif name == "missing_or_empty_secret_file:llm_api_key":
        secrets[1].write_bytes(b"")
    elif name == "netease_submodule_not_initialized":
        (checkout / "netease-music-api/app.js").unlink()
    else:
        snapshot = tmp_path / "other-1.snapshot"
        snapshot.write_bytes(b"snapshot")
    with pytest.raises(ValueError, match=name):
        ecs_preflight.check_inputs(checkout, artifacts, dump, sha(b"dump"), snapshot,
                                   sha(b"snapshot"), env_file, secrets)


def test_preflight_rejects_unsupported_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(ecs_preflight.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ecs_preflight.platform, "machine", lambda: "aarch64")
    with pytest.raises(ValueError, match="linux_amd64"):
        ecs_preflight.check_inputs(tmp_path, tmp_path, tmp_path, "0" * 64,
                                   tmp_path / "approved-1.snapshot", "0" * 64,
                                   tmp_path, (tmp_path, tmp_path, tmp_path), check_platform=True)
