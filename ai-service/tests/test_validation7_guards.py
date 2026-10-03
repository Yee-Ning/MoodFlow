import json

import pytest

from scripts.validation7_guards import (
    SpendingGate,
    current_run_playlists,
    require_owned_playlist,
    require_test_name,
    require_test_user,
)


def test_account_and_current_run_ownership():
    require_test_user("17994646607")
    require_owned_playlist(123, {123: "MoodFlowTest-20261003-120000"})
    for identity in (None, "main", "17994646608"):
        with pytest.raises(ValueError):
            require_test_user(identity)
    for identifier in (122, "123", True):
        with pytest.raises(ValueError):
            require_owned_playlist(identifier, {123: "current run"})


def test_readback_keeps_current_run_ids_and_rejects_historical_name_matches():
    items = [{"id": 123, "name": "new"}, {"id": 456, "name": "MoodFlowTest-20261003-120000"}]
    assert current_run_playlists(items, {123: "new"}) == [items[0]]
    assert current_run_playlists([], {123: "new"}) == []
    with pytest.raises(KeyError):
        current_run_playlists([{"playlistId": 123}], {123: "new"})


@pytest.mark.parametrize("name", ["MoodFlowTest-20261003-120000", "MoodFlowTest-20261003-120000-crash"])
def test_names_accept_only_timestamped_test_prefix(name):
    require_test_name(name)


@pytest.mark.parametrize("name", ["old playlist", "MoodFlowTest-old", "MoodFlowTest-20261003-120000\n", None])
def test_names_reject_historical_or_invalid_targets(name):
    with pytest.raises(ValueError):
        require_test_name(name)


def test_spending_ceiling_survives_a_new_process_instance(tmp_path):
    path = tmp_path / "ceiling.json"
    gate = SpendingGate(path, cap=0.009)
    first = gate.reserve("deepseek", {"model": "deepseek-flash", "max_tokens": 1024}, 100)
    assert first["ceiling_cny"] == pytest.approx(0.008392)
    with pytest.raises(ValueError):
        SpendingGate(path, cap=0.009).reserve("typesafe", {}, 2000)
    assert json.loads(path.read_text())["calls"] == 1


@pytest.mark.parametrize("provider,payload,size", [
    ("unknown", {}, 10), ("deepseek", {"model": "other", "max_tokens": 1}, 1),
    ("deepseek", {"model": "deepseek-flash"}, 1),
    ("deepseek", {"model": "deepseek-flash", "max_tokens": 4097}, 1),
    ("typesafe", {}, -1), ("typesafe", {}, 128001),
])
def test_unknown_or_unbounded_cost_fails_before_dispatch(tmp_path, provider, payload, size):
    with pytest.raises(ValueError):
        SpendingGate(tmp_path / "ceiling.json").reserve(provider, payload, size)
    assert not (tmp_path / "ceiling.json").exists()
