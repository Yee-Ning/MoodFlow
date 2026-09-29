"""Backup path, retention, and schema safety checks without Docker."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from deploy import backup, backup_qdrant_api


def test_safe_file_rejects_missing_empty_and_outside(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    with pytest.raises(backup.BackupError, match="unsafe_or_missing"):
        backup.safe_file(allowed / "missing.dump", allowed)
    empty = allowed / "empty.dump"
    empty.touch()
    with pytest.raises(backup.BackupError, match="empty_backup"):
        backup.safe_file(empty, allowed)
    outside = tmp_path / "outside.dump"
    outside.write_bytes(b"data")
    with pytest.raises(backup.BackupError, match="unsafe_or_missing"):
        backup.safe_file(outside, allowed)


def test_retention_only_deletes_verified_files_inside_backup_root(monkeypatch, tmp_path):
    root = tmp_path / "backups"
    pg_dir, qd_dir, manifests = [root / name for name in ("postgres", "qdrant", "manifests")]
    for directory in (pg_dir, qd_dir, manifests):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(backup, "POSTGRES_DIR", pg_dir)
    monkeypatch.setattr(backup, "QDRANT_DIR", qd_dir)
    monkeypatch.setattr(backup, "MANIFEST_DIR", manifests)
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)

    def add(days_old, suffix, *, corrupt=False):
        then = now - timedelta(days=days_old)
        stamp = then.strftime("%Y%m%dT%H%M%SZ") + f"-{suffix:08x}"
        pg = pg_dir / (stamp + ".dump")
        qd = qd_dir / (stamp + ".snapshot")
        pg.write_bytes(b"pg")
        qd.write_bytes(b"qd")
        manifest = {"status": "complete", "restore_verified": True,
                    "timestamp_utc": then.isoformat(),
                    "postgres": backup.file_entry(pg), "qdrant": backup.file_entry(qd)}
        if corrupt:
            manifest["postgres"]["sha256"] = "0" * 64
        path = manifests / (stamp + ".json")
        path.write_text(json.dumps(manifest))
        return path, pg, qd

    recent = add(1, 1)
    stale = add(60, 2)
    corrupt = add(61, 3, corrupt=True)
    removed = backup.retention(now)
    assert stale[0] in removed
    assert all(path.exists() for path in recent)
    assert all(not path.exists() for path in stale)
    assert all(path.exists() for path in corrupt)


def test_qdrant_schema_preserves_dense_sparse_and_points():
    info = {"result": {"status": "green", "points_count": 41,
                       "config": {"params": {"vectors": {"dense": {"size": 1024,
                                                                       "distance": "Cosine"}},
                                             "sparse_vectors": {"bm25": {"modifier": "idf"}}}}}}
    assert backup.qdrant_schema(info) == backup_qdrant_api.schema(info)
    assert backup.qdrant_schema(info)["points_count"] == 41


def test_hash_and_entry_detect_corruption(tmp_path):
    path = tmp_path / "one.dump"
    path.write_bytes(b"backup")
    entry = backup.file_entry(path)
    assert entry["sha256"] == hashlib.sha256(b"backup").hexdigest()
    backup.verified_entry(entry, tmp_path)
    path.write_bytes(b"broken")
    with pytest.raises(backup.BackupError, match="integrity_failed"):
        backup.verified_entry(entry, tmp_path)


def test_snapshot_filename_blocks_path_traversal():
    assert backup.SAFE_NAME.fullmatch("worldline-2026.snapshot")
    assert not backup.SAFE_NAME.fullmatch("../outside.snapshot")
    assert not backup.SAFE_NAME.fullmatch("folder/name.snapshot")
