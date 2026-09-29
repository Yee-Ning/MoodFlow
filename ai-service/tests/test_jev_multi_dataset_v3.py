import hashlib

from evals.jev_multi_dataset_v3 import BLIND, DEV, SINGLE_BLIND, build
from evals.jev_shadow_v1 import read_jsonl


def test_v3_corpora_are_frozen_and_keep_historical_seed_in_dev_only():
    paths = {"multi_dev_v3": (DEV, "4d5eba1b34142593fde46997e355fdea62885f43b48de3988d7f81129174eb0c"),
             "multi_blind_test_v3": (BLIND, "137e9e3c479b8f1d3a309fbf1e03142c79b55dc4933d34f55bcb600c78dedc94"),
             "single_blind_v3": (SINGLE_BLIND, "8ca3cf3aca26530a3b8bfdcc9b83b0828bede2ba5b7916eb6c6f5caaeaf0118e")}
    built = build()
    for name, (path, expected_hash) in paths.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
        assert read_jsonl(path) == built[name]
    assert sum(row["source"] == "router_eval_gold_v2" for row in built["multi_dev_v3"]) == 11
    assert all(row["source"] != "router_eval_gold_v2" for row in built["multi_blind_test_v3"])


def test_multi_corpus_covers_all_pairs_triples_and_hard_boundaries():
    dev, blind = build()["multi_dev_v3"], build()["multi_blind_test_v3"]
    pairs = {frozenset(row["gold_routes"]) for row in dev if len(row["gold_routes"]) == 2}
    assert len(pairs) == 6
    triples = {frozenset(row["gold_routes"]) for row in dev if len(row["gold_routes"]) == 3}
    assert len(triples) == 4
    for rows, true_multi, single_guard, partial in ((dev, 33, 5, 3), (blind, 15, 5, 4)):
        assert sum(len(row["gold_routes"]) > 1 for row in rows) == true_multi
        assert sum(row["facet"].startswith("single_guard") for row in rows) == single_guard
        assert sum(bool(row["unroutable_part"]) for row in rows) == partial
        assert all(row["capability_reason"] for row in rows)
