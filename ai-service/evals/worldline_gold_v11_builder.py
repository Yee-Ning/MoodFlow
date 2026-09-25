"""Build the reviewed WorldLine Gold V1.1 boundary dataset."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from evals.worldline_gold_v1_loader import load_worldline_gold_dataset
from evals.worldline_gold_v1_models import (
    DATASET_KIND,
    DATASET_VERSION_V11,
    SCHEMA_VERSION,
    GoldCase,
    GoldSourceLedgerEntry,
    content_sha256,
)


SERVICE_ROOT = Path(__file__).resolve().parents[1]
BASE_DATASET = SERVICE_ROOT / "evals" / "datasets" / "worldline_gold_v1.jsonl"
BASE_SOURCES = SERVICE_ROOT / "evals" / "fixtures" / "worldline_gold_v1_sources.jsonl"
OUTPUT_DATASET = SERVICE_ROOT / "evals" / "datasets" / "worldline_gold_v11.jsonl"
OUTPUT_SOURCES = SERVICE_ROOT / "evals" / "fixtures" / "worldline_gold_v11_sources.jsonl"
REVIEW_TIME = "2026-09-25T08:00:00Z"


@dataclass(frozen=True)
class GoldV11BuildResult:
    base_case_count: int
    added_case_count: int
    total_case_count: int
    accepted_count: int
    rejected_count: int
    source_count: int
    dataset_sha256: str
    source_ledger_sha256: str


def _fixture_source_id(source_id: str) -> str:
    digest = sha256(source_id.encode("utf-8")).hexdigest()[:24]
    return f"fixture:worldline-gold-v11:{digest}"


def _source(
    *,
    source_id: str,
    title: str,
    publisher: str,
    url: str,
    language: str,
    source_type: str,
    content: str,
    provenance: str,
) -> GoldSourceLedgerEntry:
    return GoldSourceLedgerEntry.model_validate(
        {
            "source_id": source_id,
            "fixture_source_id": _fixture_source_id(source_id),
            "title": title,
            "publisher": publisher,
            "url": url,
            "provenance": provenance,
            "language": language,
            "source_type": source_type,
            "content": content,
            "content_hash": content_sha256(content),
        }
    )


def _evidence(source: GoldSourceLedgerEntry, exact_span: str) -> dict[str, Any]:
    start = source.content.index(exact_span)
    return {
        "source_id": source.source_id,
        "source_title": source.title,
        "publisher": source.publisher,
        "source_url": str(source.url) if source.url else None,
        "source_type": source.source_type,
        "language": source.language,
        "exact_span": exact_span,
        "evidence_text": source.content,
        "span_start": start,
        "span_end": start + len(exact_span),
        "content_sha256": source.content_hash,
        "fixture_source_id": source.fixture_source_id,
    }


def _catalog(entity_id: str, name: str) -> dict[str, str]:
    return {
        "provider": "netease",
        "entity_id": entity_id,
        "entity_type": "artist",
        "name": name,
    }


def _review(notes: str) -> dict[str, Any]:
    return {
        "status": "approved",
        "method": "gold_v11_evidence_boundary_review",
        "relation_reviewer": "gold-v11-boundary-review",
        "identity_reviewer": "gold-v11-boundary-review",
        "adjudicator": None,
        "reviewed_at": REVIEW_TIME,
        "notes": notes,
        "reviewer_notes": notes,
        "promotion_time": REVIEW_TIME,
    }


def _case(
    *,
    case_id: str,
    region: str,
    tags: list[str],
    canonical_name: str,
    knowledge_entity_id: str,
    aliases: list[str],
    catalog_reference: dict[str, str] | None,
    genre_id: str,
    genre_name: str,
    evidence: list[dict[str, Any]],
    outcome: str,
    reason: str,
    identity: dict[str, Any],
    evaluation: dict[str, Any],
    review_notes: str,
) -> GoldCase:
    return GoldCase.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "dataset_version": DATASET_VERSION_V11,
            "dataset_kind": DATASET_KIND,
            "case_id": case_id,
            "region": region,
            "tags": tags,
            "artist": {
                "canonical_name": canonical_name,
                "knowledge_entity_id": knowledge_entity_id,
                "aliases": aliases,
                "registry_scope": "fixture_only_registry",
                "catalog_reference": catalog_reference,
            },
            "relation": {
                "source_entity_id": knowledge_entity_id,
                "target_genre": {
                    "canonical_id": genre_id,
                    "canonical_name": genre_name,
                    "registry_version": "v2",
                },
                "relation_type": "associated_with",
            },
            "evidence": evidence,
            "expected": {
                "outcome": outcome,
                "reason": reason,
                "runtime_relation": "associated_with",
                "runtime_rejection_reason": (
                    reason if outcome == "rejected" else None
                ),
            },
            "identity": identity,
            "review": _review(review_notes),
            "evaluation": evaluation,
        }
    )


def _resolved_identity(catalog_node_id: str, source_ids: list[str]) -> dict[str, Any]:
    return {
        "knowledge_status": "resolved",
        "catalog_status": "resolved",
        "ambiguity_status": "unambiguous",
        "expected_catalog_node_id": catalog_node_id,
        "identity_evidence_source_ids": source_ids,
    }


def _clone_boundary_case(
    base: GoldCase,
    *,
    case_id: str,
    tags: list[str],
    evaluation: dict[str, Any],
    notes: str,
) -> GoldCase:
    payload = base.model_dump(mode="json", by_alias=True)
    payload.update(
        {
            "dataset_version": DATASET_VERSION_V11,
            "case_id": case_id,
            "tags": tags,
            "evaluation": evaluation,
            "review": _review(notes),
        }
    )
    return GoldCase.model_validate(payload)


def _new_sources() -> list[GoldSourceLedgerEntry]:
    return [
        _source(
            source_id="src:sony-music-taiwan:jolin-karma-20190506",
            title="Jolin蔡依林MV化身「復仇女王蜂」",
            publisher="Sony Music Taiwan",
            url="https://www.sonymusic.com.tw/news/20190506-jolin-karma/",
            language="zh-Hant",
            source_type="official_label_editorial",
            content="華語樂壇天后Jolin蔡依林",
            provenance="batch_a_reviewed_boundary:jolin-tsai",
        ),
        _source(
            source_id="src:core:jay-chou-r-and-b-boundary",
            title="Jay Chou's Zhongguo Feng: Songs and Identity",
            publisher="University of Malaya Students Repository / CORE",
            url="https://core.ac.uk/download/268879767.pdf",
            language="en",
            source_type="academic_thesis_excerpt",
            content=(
                "No matter how close his music approaches to the R&B, his music is "
                "something created by himself through bringing together all the "
                "cultural elements at home and abroad, a hybrid music."
            ),
            provenance="project_knowledge_source:jc-v2-045",
        ),
        _source(
            source_id="src:apple-music:mc-hotdog-151611994",
            title="MC HotDog - Apple Music",
            publisher="Apple Music",
            url="https://music.apple.com/us/artist/mc-hotdog/151611994",
            language="en",
            source_type="streaming_catalog_editorial",
            content="MC HotDog Essentials\nApple Music Hip-Hop",
            provenance="gold-v11-reviewed-source:mc-hotdog",
        ),
        _source(
            source_id="src:apple-music:mc-hotdog-wake-up-560990337",
            title="Wake Up - Album by MC HotDog",
            publisher="Apple Music",
            url="https://music.apple.com/us/album/wake-up/560990337",
            language="en",
            source_type="streaming_album_editorial",
            content="Wake Up\nMC HotDog\nChinese Hip-Hop · 2006",
            provenance="gold-v11-reviewed-boundary:album-genre-scope",
        ),
        _source(
            source_id="src:netease-search:dynamic-duo-exact-collision",
            title="NetEase artist search snapshot: Dynamic Duo",
            publisher="NetEase Cloud Music",
            url=(
                "https://music.163.com/api/search/get/web?s=Dynamic%20Duo"
                "&type=100&offset=0&total=true&limit=10"
            ),
            language="en",
            source_type="catalog_search_snapshot",
            content=(
                '{"artist_count":25,"artists":['
                '{"id":126610,"name":"Dynamic Duo"},'
                '{"id":46363179,"name":"Dynamic Duo"},'
                '{"id":13263104,"name":"The Dynamic Duo"},'
                '{"id":147065,"name":"Dynamic Duo"},'
                '{"id":30889945,"name":"Real Dynamic Duo"},'
                '{"id":122427498,"name":"Dynamic Duo"},'
                '{"id":33115285,"name":"The Dynamic Duo And Company"},'
                '{"id":47447556,"name":"Dynamic Duo"},'
                '{"id":100058747,"name":"Dynamic Duo"},'
                '{"id":46324747,"name":"Dynamic Duo"}],'
                '"query":"Dynamic Duo"}'
            ),
            provenance="gold-v11-catalog-review:dynamic-duo:2026-09-25",
        ),
        _source(
            source_id="src:netease-search:common-legal-name-empty",
            title="NetEase artist search snapshot: Lonnie Rashid Lynn",
            publisher="NetEase Cloud Music",
            url=(
                "https://music.163.com/api/search/get/web?s=Lonnie%20Rashid%20Lynn"
                "&type=100&offset=0&total=true&limit=10"
            ),
            language="en",
            source_type="catalog_search_snapshot",
            content=(
                '{"artist_count":0,"artists":[],"query":"Lonnie Rashid Lynn"}'
            ),
            provenance="gold-v11-catalog-review:common:2026-09-25",
        ),
    ]


def _new_cases(
    base_by_id: dict[str, GoldCase],
    source_by_id: dict[str, GoldSourceLedgerEntry],
) -> list[GoldCase]:
    jolin_source = source_by_id["src:sony-music-taiwan:jolin-karma-20190506"]
    jay_boundary = source_by_id["src:core:jay-chou-r-and-b-boundary"]
    hotdog_source = source_by_id["src:apple-music:mc-hotdog-151611994"]
    hotdog_album_source = source_by_id[
        "src:apple-music:mc-hotdog-wake-up-560990337"
    ]
    dynamic_source = source_by_id[
        "src:netease-search:dynamic-duo-exact-collision"
    ]
    common_source = source_by_id[
        "src:netease-search:common-legal-name-empty"
    ]
    jay = base_by_id["wl-gold-v1-jay-chou-c-pop"]
    beyonce = base_by_id["wl-gold-v1-beyonce-r-and-b"]
    return [
        _case(
            case_id="wl-gold-v11-jolin-tsai-c-pop-not-explicit",
            region="cjk_chinese",
            tags=["artist_to_genre", "relation_not_explicit"],
            canonical_name="蔡依林",
            knowledge_entity_id="artist:jolin_tsai",
            aliases=["Jolin Tsai", "Jolin Cai"],
            catalog_reference=_catalog("7219", "蔡依林"),
            genre_id="genre:c_pop",
            genre_name="C-pop",
            evidence=[_evidence(jolin_source, jolin_source.content)],
            outcome="rejected",
            reason="relation_not_explicit",
            identity=_resolved_identity(
                "netease:artist:7219",
                [jolin_source.source_id],
            ),
            evaluation={
                "task": "semantic_expansion",
                "scenario": "semantic_wrong_predicate",
                "expected_outcome": "rejected",
                "candidate_count": 0,
                "rejection_reasons": [],
            },
            review_notes=(
                "The phrase places Jolin Tsai in the Chinese-language music scene "
                "but does not explicitly classify the artist as registry genre C-pop."
            ),
        ),
        _case(
            case_id="wl-gold-v11-nujabes-jazz-hip-hop-no-approved-evidence",
            region="japanese",
            tags=["artist_to_genre", "no_direct_evidence"],
            canonical_name="Nujabes",
            knowledge_entity_id="artist:nujabes",
            aliases=["瀬葉淳"],
            catalog_reference=_catalog("162138", "Nujabes"),
            genre_id="genre:jazz_hip_hop",
            genre_name="Jazz Hip-Hop",
            evidence=[],
            outcome="rejected",
            reason="no_approved_direct_evidence",
            identity=_resolved_identity("netease:artist:162138", []),
            evaluation={
                "task": "semantic_expansion",
                "scenario": "semantic_no_evidence",
                "expected_outcome": "rejected",
                "candidate_count": 0,
                "rejection_reasons": [],
            },
            review_notes=(
                "No source passed the Gold source-quality gate. This label means "
                "do not expand from current evidence; it does not assert the real-world "
                "relation is false."
            ),
        ),
        _case(
            case_id="wl-gold-v11-jay-chou-r-and-b-influence-boundary",
            region="cjk_chinese",
            tags=["artist_to_genre", "wrong_relation_scope"],
            canonical_name="周杰伦",
            knowledge_entity_id="artist:jay_chou",
            aliases=["周杰倫", "Jay Chou"],
            catalog_reference=_catalog("6452", "周杰伦"),
            genre_id="genre:r_and_b",
            genre_name="R&B",
            evidence=[_evidence(jay_boundary, jay_boundary.content)],
            outcome="rejected",
            reason="style_proximity_does_not_establish_artist_genre",
            identity=_resolved_identity(
                "netease:artist:6452",
                [jay_boundary.source_id],
            ),
            evaluation={
                "task": "semantic_expansion",
                "scenario": "semantic_wrong_predicate",
                "expected_outcome": "rejected",
                "candidate_count": 0,
                "rejection_reasons": [],
            },
            review_notes=(
                "The source describes musical proximity to R&B and immediately frames "
                "the work as hybrid; it is not a direct artist-level genre assertion."
            ),
        ),
        _case(
            case_id="wl-gold-v11-mc-hotdog-hip-hop-duplicate-evidence",
            region="cjk_chinese",
            tags=["artist_to_genre", "duplicate_evidence_boundary"],
            canonical_name="MC HotDog",
            knowledge_entity_id="artist:mc_hotdog",
            aliases=["MC HotDog 热狗", "MC HotDog 熱狗", "姚中仁"],
            catalog_reference=_catalog("4479", "MC HotDog 热狗"),
            genre_id="genre:hip_hop",
            genre_name="Hip-Hop",
            evidence=[_evidence(hotdog_source, hotdog_source.content)],
            outcome="accepted",
            reason="reviewed_direct_artist_playlist_classification",
            identity=_resolved_identity(
                "netease:artist:4479",
                [hotdog_source.source_id],
            ),
            evaluation={
                "task": "semantic_expansion",
                "scenario": "semantic_duplicate_evidence",
                "expected_outcome": "accepted",
            },
            review_notes=(
                "The reviewed Apple Music artist playlist directly classifies MC HotDog "
                "under Hip-Hop; duplicated supported claims must project one relation."
            ),
        ),
        _case(
            case_id="wl-gold-v11-mc-hotdog-album-genre-scope",
            region="cjk_chinese",
            tags=["artist_to_genre", "album_genre_scope"],
            canonical_name="MC HotDog",
            knowledge_entity_id="artist:mc_hotdog",
            aliases=["MC HotDog 热狗", "MC HotDog 熱狗", "姚中仁"],
            catalog_reference=_catalog("4479", "MC HotDog 热狗"),
            genre_id="genre:hip_hop",
            genre_name="Hip-Hop",
            evidence=[_evidence(hotdog_album_source, hotdog_album_source.content)],
            outcome="rejected",
            reason="album_genre_does_not_establish_artist_genre",
            identity=_resolved_identity(
                "netease:artist:4479",
                [hotdog_album_source.source_id],
            ),
            evaluation={
                "task": "semantic_expansion",
                "scenario": "semantic_wrong_predicate",
                "expected_outcome": "rejected",
                "candidate_count": 0,
                "rejection_reasons": [],
            },
            review_notes=(
                "The source directly classifies the album Wake Up as Chinese Hip-Hop. "
                "An album-level genre label alone is insufficient for a new "
                "artist-level associated_with edge."
            ),
        ),
        _case(
            case_id="wl-gold-v11-dynamic-duo-identity-ambiguous",
            region="korean",
            tags=["identity", "catalog_mapping_ambiguous"],
            canonical_name="Dynamic Duo",
            knowledge_entity_id="artist:dynamic_duo",
            aliases=["다이나믹 듀오"],
            catalog_reference=None,
            genre_id="genre:hip_hop",
            genre_name="Hip-Hop",
            evidence=[_evidence(dynamic_source, dynamic_source.content)],
            outcome="rejected",
            reason="catalog_mapping_ambiguous",
            identity={
                "knowledge_status": "resolved",
                "catalog_status": "ambiguous",
                "ambiguity_status": "name_collision",
                "expected_catalog_node_id": None,
                "identity_evidence_source_ids": [dynamic_source.source_id],
            },
            evaluation={
                "task": "entity_resolution",
                "scenario": "identity_ambiguous",
                "expected_outcome": "ambiguous",
                "expected_reason": "catalog_artist_ambiguous",
                "catalog_candidates": [
                    _catalog("126610", "Dynamic Duo"),
                    _catalog("46363179", "Dynamic Duo"),
                ],
            },
            review_notes=(
                "The frozen NetEase search snapshot contains multiple exact-name artist "
                "IDs. No candidate may enter an executable graph without adjudication."
            ),
        ),
        _case(
            case_id="wl-gold-v11-common-catalog-mapping-missing",
            region="western",
            tags=["identity", "catalog_mapping_missing"],
            canonical_name="Common",
            knowledge_entity_id="artist:common",
            aliases=["Lonnie Rashid Lynn"],
            catalog_reference=None,
            genre_id="genre:hip_hop",
            genre_name="Hip-Hop",
            evidence=[_evidence(common_source, common_source.content)],
            outcome="rejected",
            reason="catalog_mapping_missing_in_reviewed_candidate_set",
            identity={
                "knowledge_status": "resolved",
                "catalog_status": "unresolved",
                "ambiguity_status": "unambiguous",
                "expected_catalog_node_id": None,
                "identity_evidence_source_ids": [common_source.source_id],
            },
            evaluation={
                "task": "entity_resolution",
                "scenario": "identity_missing",
                "expected_outcome": "unresolved",
                "expected_reason": "catalog_artist_not_found",
                "catalog_candidates": [],
            },
            review_notes=(
                "The frozen legal-name query returned no catalog candidates. The label "
                "is scoped to this reviewed candidate set and does not claim the artist "
                "is absent from the provider."
            ),
        ),
        _clone_boundary_case(
            jay,
            case_id="wl-gold-v11-jay-chou-c-pop-disabled-action",
            tags=["disabled_genre_action", "c_pop"],
            evaluation={
                "task": "genre_exploration",
                "scenario": "genre_disabled",
                "tier": "session_replay",
                "expected_outcome": "rejected",
                "error_code": "genre_not_executable",
            },
            notes=(
                "C-pop is registered but disabled in registry v2; a server-generated "
                "enabled exploration action must not be exposed."
            ),
        ),
        _clone_boundary_case(
            beyonce,
            case_id="wl-gold-v11-beyonce-r-and-b-disabled-action",
            tags=["disabled_genre_action", "r_and_b"],
            evaluation={
                "task": "genre_exploration",
                "scenario": "genre_disabled",
                "tier": "session_replay",
                "expected_outcome": "rejected",
                "error_code": "genre_not_executable",
            },
            notes=(
                "R&B is registered but disabled in registry v2; the action gate must "
                "remain closed even when the underlying relation is reviewed."
            ),
        ),
        _clone_boundary_case(
            jay,
            case_id="wl-gold-v11-jay-chou-source-conflict-atomicity",
            tags=["failure_atomicity", "source_conflict"],
            evaluation={
                "task": "graph_merge",
                "scenario": "merge_source_conflict",
                "expected_outcome": "rejected",
                "error_code": "source_conflict",
            },
            notes=(
                "A conflicting payload for an existing reviewed source must reject the "
                "merge and preserve the previous graph and evidence snapshot."
            ),
        ),
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def build_worldline_gold_v11(
    *,
    dataset_path: Path = OUTPUT_DATASET,
    source_ledger_path: Path = OUTPUT_SOURCES,
) -> GoldV11BuildResult:
    base = load_worldline_gold_dataset(BASE_DATASET, BASE_SOURCES)
    base_cases = [
        GoldCase.model_validate(
            {
                **case.model_dump(mode="json", by_alias=True),
                "dataset_version": DATASET_VERSION_V11,
            }
        )
        for case in base.cases
    ]
    sources = [*base.sources, *_new_sources()]
    source_by_id = {source.source_id: source for source in sources}
    base_by_id = {case.case_id: case for case in base_cases}
    added = _new_cases(base_by_id, source_by_id)
    cases = [*base_cases, *added]
    _write_jsonl(
        dataset_path,
        [case.model_dump(mode="json", by_alias=True) for case in cases],
    )
    _write_jsonl(
        source_ledger_path,
        [source.model_dump(mode="json") for source in sources],
    )
    loaded = load_worldline_gold_dataset(dataset_path, source_ledger_path)
    return GoldV11BuildResult(
        base_case_count=len(base_cases),
        added_case_count=len(added),
        total_case_count=len(loaded.cases),
        accepted_count=sum(case.expected.outcome == "accepted" for case in loaded.cases),
        rejected_count=sum(case.expected.outcome == "rejected" for case in loaded.cases),
        source_count=len(loaded.sources),
        dataset_sha256=loaded.dataset_sha256,
        source_ledger_sha256=loaded.source_ledger_sha256,
    )


def main() -> int:
    print(
        json.dumps(
            asdict(build_worldline_gold_v11()),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
