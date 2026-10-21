"""Anonymous local model bootstrap preserves the original credential guard."""

from pathlib import Path

import pytest

from tools.v52_offline_runtime import run_baseline as run
from evals import rag_h2_metadata_v5 as h2


def test_hf_public_credential_interfaces_return_none_and_restore(monkeypatch):
    import huggingface_hub
    import huggingface_hub.constants
    import huggingface_hub.utils
    from huggingface_hub.utils import _auth, _headers

    modules = (huggingface_hub, huggingface_hub.utils, _auth, _headers)

    def forbidden_lookup():
        raise AssertionError("Credential lookup must not execute")

    for module in modules:
        monkeypatch.setattr(module, "get_token", forbidden_lookup)
    with run.anonymous_local_models():
        assert huggingface_hub.constants.HF_HUB_OFFLINE
        assert huggingface_hub.constants.HF_HUB_DISABLE_IMPLICIT_TOKEN
        assert all(module.get_token() is None for module in modules)
        assert _headers.get_token_to_send(None) is None
    assert all(module.get_token is forbidden_lookup for module in modules)


def test_existing_guard_still_refuses_token_files(tmp_path):
    guard = h2.ReadOnlyGuard()
    with (
        run.anonymous_local_models(),
        pytest.raises(h2.SafetyStop, match="credential_file"),
    ):
        guard.audit("open", (str(tmp_path / "token"), "r", 0))
    assert guard.denied == ["credential_file_open"]


def test_wrapper_only_runs_retrieval(monkeypatch):
    monkeypatch.setattr(run.sys, "argv", [str(Path(run.__file__)), "generate"])
    with pytest.raises(ValueError, match="only_accepts"):
        run.main()
