import hashlib
from unittest.mock import MagicMock, patch

import pytest

from deploy import restore_qdrant_snapshot


def test_restore_only_approved_snapshot_into_empty_collection(tmp_path, monkeypatch):
    lock = tmp_path / "lock.json"
    lock.write_text('{"collection_name":"approved"}')
    monkeypatch.setattr(restore_qdrant_snapshot, "LOCK_PATH", lock)
    snapshot = tmp_path / "approved-1.snapshot"
    snapshot.write_bytes(b"fixture")
    secret = tmp_path / "key"
    secret.write_text("fixture-key")
    client = MagicMock()
    client.get.side_effect = [MagicMock(status_code=404),
                              MagicMock(json=lambda: {"result": {"status": "green", "points_count": 41}})]
    client.post.return_value.json.return_value = {"result": True}
    with patch.object(restore_qdrant_snapshot.httpx, "Client") as factory:
        factory.return_value.__enter__.return_value = client
        result = restore_qdrant_snapshot.restore(snapshot, hashlib.sha256(b"fixture").hexdigest(),
                                                 endpoint="http://qdrant:6333", secret_file=secret)
    assert result == {"restored": True, "collection": "approved", "points": 41}
    assert client.post.call_args.kwargs["params"]["priority"] == "snapshot"
    assert client.post.call_args.kwargs["params"]["checksum"] == hashlib.sha256(b"fixture").hexdigest()


@pytest.mark.parametrize("case", ["wrong_collection", "wrong_hash", "existing_collection", "not_green"])
def test_restore_fails_closed(tmp_path, monkeypatch, case):
    lock = tmp_path / "lock.json"
    lock.write_text('{"collection_name":"approved"}')
    monkeypatch.setattr(restore_qdrant_snapshot, "LOCK_PATH", lock)
    snapshot = tmp_path / ("other-1.snapshot" if case == "wrong_collection" else "approved-1.snapshot")
    snapshot.write_bytes(b"fixture")
    secret = tmp_path / "key"
    secret.write_text("fixture-key")
    client = MagicMock()
    client.get.side_effect = [MagicMock(status_code=200 if case == "existing_collection" else 404),
                              MagicMock(json=lambda: {"result": {"status": "red", "points_count": 41}})]
    client.post.return_value.json.return_value = {"result": True}
    with patch.object(restore_qdrant_snapshot.httpx, "Client") as factory:
        factory.return_value.__enter__.return_value = client
        with pytest.raises(ValueError):
            restore_qdrant_snapshot.restore(snapshot, "0" * 64 if case == "wrong_hash"
                                            else hashlib.sha256(b"fixture").hexdigest(),
                                            endpoint="http://qdrant:6333", secret_file=secret)
    if case in {"wrong_collection", "wrong_hash"}:
        client.post.assert_not_called()
    if case == "existing_collection":
        client.post.assert_not_called()
