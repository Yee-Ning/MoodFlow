import hashlib
import json
from unittest.mock import Mock

import pytest

from scripts import setup


def bundle(root, data=b"model-weights"):
    target = root / "ai-service/artifacts/intent_model"
    target.mkdir(parents=True)
    (target / "weights").write_bytes(data)
    (target / "manifest.json").write_text(json.dumps({
        "files": {"weights": hashlib.sha256(data).hexdigest()},
    }), encoding="utf-8")
    return target


def test_valid_model_bundle_and_tamper_detection(tmp_path):
    target = bundle(tmp_path)
    assert setup.verify_models(tmp_path) == 1
    (target / "weights").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        setup.verify_models(tmp_path)


def test_pointer_and_missing_weights_are_actionable(tmp_path):
    target = bundle(tmp_path, b"version https://git-lfs.github.com/spec/v1\noid sha256:example")
    with pytest.raises(ValueError, match="LFS pointers"):
        setup.verify_models(tmp_path)
    (target / "weights").unlink()
    with pytest.raises(ValueError, match="Missing model file"):
        setup.verify_models(tmp_path)


def test_model_manifest_cannot_escape_its_directory(tmp_path):
    target = bundle(tmp_path)
    (target / "manifest.json").write_text(json.dumps({"files": {"../../outside": "x"}}))
    with pytest.raises(ValueError, match="outside"):
        setup.verify_models(tmp_path)


def test_setup_copies_example_and_preserves_existing_environment(tmp_path):
    (tmp_path / ".env.example").write_text("LLM_API_KEY=\n", encoding="utf-8")
    assert setup.create_environment(tmp_path)
    (tmp_path / ".env").write_text("LOCAL_TEST_VALUE=keep\n", encoding="utf-8")
    assert not setup.create_environment(tmp_path)
    assert (tmp_path / ".env").read_text() == "LOCAL_TEST_VALUE=keep\n"


def test_missing_template_does_not_leave_an_empty_environment(tmp_path):
    with pytest.raises(FileNotFoundError):
        setup.create_environment(tmp_path)
    assert not (tmp_path / ".env").exists()


def test_check_mode_is_read_only_and_reports_missing_prerequisites(tmp_path, monkeypatch):
    bundle(tmp_path)
    (tmp_path / "netease-music-api").mkdir()
    (tmp_path / "netease-music-api/app.js").write_text("// fixture")
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup.shutil, "which", lambda _: "executable")
    run = Mock()
    monkeypatch.setattr(setup, "run", run)
    assert setup.main(["--check"]) == 0
    assert not (tmp_path / ".env").exists()
    assert all(call.args[0][0] == "docker" for call in run.call_args_list)
    monkeypatch.setattr(setup.shutil, "which", lambda _: None)
    assert setup.main(["--check"]) == 1
