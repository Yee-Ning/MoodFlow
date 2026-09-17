"""Offline diagnostic capture must not enlarge the actual retrieval input."""

import asyncio

from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit
from run_rag_hybrid_shadow_baseline import (
    DiagnosticBackend,
    _failure_category,
    _ids,
    _read_jsonl,
    _write_dict_jsonl,
)


class FakeBackend:
    def __init__(self):
        self.limits = []

    async def validate_binding(self, binding):
        return None

    async def search(self, binding, legs, query_filter, *, limit):
        self.limits.append(limit)
        return [LegSearchResult(
            leg=legs[0],
            hits=[RawSearchHit(point_id=str(i), score=float(100 - i), payload={})
                  for i in range(limit)],
        )]


def test_diagnostic_backend_retains_configured_leg_limit():
    delegate = FakeBackend()
    backend = DiagnosticBackend(delegate)
    actual = asyncio.run(backend.search(None, [object()], None, limit=80))
    assert delegate.limits == [80, 100]
    assert len(actual[0].hits) == 80
    assert len(backend.results[0].hits) == 80
    assert len(backend.diagnostic[0].hits) == 100


def test_diagnostic_backend_only_records_first_call():
    delegate = FakeBackend()
    backend = DiagnosticBackend(delegate)

    async def run():
        await backend.search(None, [object()], None, limit=80)
        await backend.search(None, [object()], None, limit=80)

    asyncio.run(run())
    assert delegate.limits == [80, 100, 80]
    assert backend.calls == 2


def test_diagnostic_ranking_slice_and_artifact_round_trip(tmp_path):
    ranking = [{"parent_id": f"parent-{i}"} for i in range(100)]
    assert len(_ids(ranking, 40)) == 40
    assert len(_ids(ranking, 100)) == 100
    path = tmp_path / "diagnostics.jsonl"
    _write_dict_jsonl(path, [{"case_id": "049", "ranking": ranking}])
    assert _read_jsonl(path) == [{"case_id": "049", "ranking": ranking}]


def test_actual_top40_overrides_non_nested_diagnostic_miss():
    assert _failure_category(
        dense100=False, bm25100=False, top40=True, top12=True, final=True
    ) == "SUCCESS"
    assert _failure_category(
        dense100=False, bm25100=False, top40=False, top12=False, final=False
    ) == "DENSE_AND_BM25_MISS"
    assert _failure_category(
        dense100=True, bm25100=False, top40=False, top12=False, final=False
    ) == "FUSION_RANKING_MISS"
