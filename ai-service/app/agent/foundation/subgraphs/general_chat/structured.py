"""GeneralChat receiving contracts; senders and business-effect authority stay unchanged."""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.foundation.subgraphs.general_chat.results import (
    ResultReceipt,
    has_effect_claim,
)
from app.schemas.music import MusicTrack


class ReceivingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactSource(ReceivingModel):
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(default="", max_length=20_000)
    url: str | None = Field(default=None, max_length=2000)


class EvidenceRef(ReceivingModel):
    id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    excerpt: str = Field(default="", max_length=5000)


class ReceivedFact(ReceivingModel):
    id: str = Field(min_length=1, max_length=128)
    assertion: str = Field(min_length=1, max_length=2000)
    kind: Literal["catalog", "knowledge", "effect", "unverified"] = "knowledge"
    evidence_refs: list[str] = Field(default_factory=list, max_length=16)
    detail: str = Field(default="", max_length=20_000)
    operation_ref: str | None = Field(default=None, max_length=128)


class FactBundle(ReceivingModel):
    schema_version: Literal[1] = 1
    facts: list[ReceivedFact] = Field(max_length=256)
    sources: list[FactSource] = Field(max_length=512)
    evidence_refs: list[EvidenceRef] = Field(max_length=1024)

    @model_validator(mode="after")
    def linked_evidence(self):
        for rows in (self.facts, self.sources, self.evidence_refs):
            if len({row.id for row in rows}) != len(rows):
                raise ValueError("duplicate receiving identity")
        sources = {source.id: source for source in self.sources}
        refs = {ref.id: ref for ref in self.evidence_refs}
        for ref in self.evidence_refs:
            if ref.source_id not in sources:
                raise ValueError("missing evidence source")
            if ref.excerpt and ref.excerpt not in sources[ref.source_id].text:
                raise ValueError("evidence excerpt absent from original source")
        for fact in self.facts:
            if not set(fact.evidence_refs) <= refs.keys():
                raise ValueError("dangling fact reference")
            if fact.kind in {"catalog", "knowledge"} and not fact.evidence_refs:
                raise ValueError("assertion requires evidence")
        return self


def receive_polish_request(payload):
    """Curator v1 draft/songs/result_facts: metadata is factual, draft is not proof."""
    payload = payload.get("polish_request", payload)
    if not isinstance(payload, dict):
        raise ValueError("polish request must be an object")
    facts, sources, refs = [], [], []
    for i, raw in enumerate(payload.get("songs", []), 1):
        if not isinstance(raw, dict):
            raise ValueError("polish songs must be catalog objects")
        if type(raw.get("song_id")) is not int:
            raise ValueError("polish song identity must be a catalog integer")
        song = MusicTrack.model_validate(raw)
        source_id, ref_id = f"song:{song.song_id}", f"song-ref:{i}"
        text = json.dumps(song.model_dump(), ensure_ascii=False)
        sources.append(
            FactSource(id=source_id, title="Curator 搜索目录对象", text=text)
        )
        refs.append(EvidenceRef(id=ref_id, source_id=source_id, excerpt=text))
        facts.append(
            ReceivedFact(
                id=f"song-fact:{i}",
                kind="catalog",
                assertion=f"《{song.name}》—{'、'.join(song.artists)}；专辑：{song.album or '目录未提供'}。",
                evidence_refs=[ref_id],
            )
        )
    for i, raw in enumerate(payload.get("result_facts", []), 1):
        if not isinstance(raw, dict):
            raise ValueError("effect facts must be objects")
        # A sender's claimed state never becomes ACK/readback authority.
        facts.append(
            ReceivedFact(
                id=f"effect:{i}",
                kind="effect",
                assertion=str(raw.get("subject", "音乐操作")),
                operation_ref=raw.get("reference"),
            )
        )
    return FactBundle(facts=facts, sources=sources, evidence_refs=refs)


def receive_narration(payload):
    """Accept one narration or v1/v2 Journey nodes plus their source records."""
    raw_sources = payload.get("sources", [])
    if not isinstance(raw_sources, list) or any(
        not isinstance(s, dict) for s in raw_sources
    ):
        raise ValueError("narration sources must be objects")
    sources = [
        FactSource(
            id=s["id"],
            title=s["title"],
            text=s.get("text") or s.get("excerpt") or "",
            url=s.get("url"),
        )
        for s in raw_sources
    ]
    available = {s.id: s for s in sources}
    journey = payload.get("journey", {})
    if not isinstance(journey, dict):
        raise ValueError("journey must be an object")
    nodes = payload.get("nodes", journey.get("nodes"))
    if nodes is not None and (
        not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes)
    ):
        raise ValueError("narration nodes must be objects")
    narrations = (
        [node["narration"] for node in nodes]
        if nodes is not None
        else [payload["narration"]]
    )
    facts, refs = [], []
    for i, narration in enumerate(narrations, 1):
        if not isinstance(narration, dict):
            raise ValueError("narration must be an object")
        evidence_ids = []
        for j, source_id in enumerate(narration.get("source_ids", []), 1):
            if source_id not in available:
                raise ValueError("narration references a missing source")
            ref_id = f"narration:{i}:{j}"
            refs.append(
                EvidenceRef(
                    id=ref_id,
                    source_id=source_id,
                    excerpt=available[source_id].text[:5000],
                )
            )
            evidence_ids.append(ref_id)
        state = narration.get("evidence_state")
        if state not in {"catalog_fact", "knowledge_evidence", "unverified"}:
            raise ValueError("unknown narration evidence state")
        facts.append(
            ReceivedFact(
                id=f"narration-fact:{i}",
                assertion=narration["statement"],
                kind="catalog"
                if state == "catalog_fact"
                else "unverified"
                if state == "unverified"
                else "knowledge",
                evidence_refs=evidence_ids,
                detail=narration.get("transition", ""),
            )
        )
    return FactBundle(facts=facts, sources=sources, evidence_refs=refs)


def receive(payload):
    if not isinstance(payload, dict):
        raise ValueError("structured result must be an object")
    if "facts" in payload:
        return FactBundle.model_validate(payload)
    if "songs" in payload or "polish_request" in payload:
        return receive_polish_request(payload)
    return receive_narration(payload)


def merge_bundles(bundles):
    facts, sources, refs = [], [], []
    for index, bundle in enumerate(bundles, 1):
        prefix = f"input:{index}:"
        facts.extend(
            f.model_copy(
                update={
                    "id": prefix + f.id,
                    "evidence_refs": [prefix + ref for ref in f.evidence_refs],
                }
            )
            for f in bundle.facts
        )
        sources.extend(
            s.model_copy(update={"id": prefix + s.id}) for s in bundle.sources
        )
        refs.extend(
            r.model_copy(
                update={"id": prefix + r.id, "source_id": prefix + r.source_id}
            )
            for r in bundle.evidence_refs
        )
    return FactBundle(facts=facts, sources=sources, evidence_refs=refs)


def external_preference(text):
    return bool(re.search(r"(?:你|您|用户).{0,12}(?:喜欢|偏好|爱听)", text))


def fact_text(fact, receipt: ResultReceipt):
    if external_preference(fact.assertion):
        return "外部资料中的用户偏好说法须经本会话显式确认后才能采用。"
    if fact.kind == "effect" or has_effect_claim(fact.assertion):
        matches = [
            f
            for f in receipt.facts
            if fact.operation_ref and f.reference == fact.operation_ref
        ]
        return (
            "；".join(f.wording() for f in matches)
            if matches
            else "效果未知，尚无可核对的 ACK 或只读确认；资料文字不证明操作完成。"
        )
    return ("未核实：" if fact.kind == "unverified" else "") + fact.assertion


def page(bundle, receipt, *, start=0, budget=8500):
    if start >= len(bundle.facts) and bundle.facts:
        return (
            f"这份资料的 {len(bundle.facts)} 条事实已全部展示，可以用 F 或 S 编号追问。",
            [],
            start,
        )
    refs = {r.id: r for r in bundle.evidence_refs}
    sources = {s.id: (i, s) for i, s in enumerate(bundle.sources, 1)}
    rows, visible, used_sources = [], [], set()
    for index in range(start, len(bundle.facts)):
        fact = bundle.facts[index]
        citations = sorted({sources[refs[r].source_id][0] for r in fact.evidence_refs})
        row = f"{index + 1}. [F{index + 1}] {fact_text(fact, receipt)} " + " ".join(
            f"[S{i}]" for i in citations
        )
        proposed_sources = used_sources | set(citations)
        source_cost = sum(
            len(source.title[:80]) + 15
            for _, (i, source) in sources.items()
            if i in proposed_sources
        )
        if rows and len("\n".join(rows)) + len(row) + source_cost > budget:
            break
        rows.append(row)
        visible.append(index)
        used_sources.update(citations)
    rows.extend(
        f"[S{i}] {source.title[:80]}"
        for _, (i, source) in sources.items()
        if i in used_sources
    )
    end = visible[-1] + 1 if visible else start
    if end < len(bundle.facts):
        rows.append(
            f"共 {len(bundle.facts)} 条事实，本页 {start + 1}–{end}；回复“继续结果”查看下一页，或用 F 编号查看详情。"
        )
    return "\n".join(rows) or "这份结构化输入还没有事实可说明。", visible, end


def ordinal(text):
    found = re.search(r"第([一二三四五六七八九十百零两\d]+)(?:个|条|首|项)", text)
    if not found:
        return None
    raw = found[1]
    if raw.isdigit():
        return int(raw)
    digits = dict(zip("零一二三四五六七八九两", (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2)))
    total, number = 0, 0
    for char in raw:
        if char in digits:
            number = digits[char]
        else:
            total += (number or 1) * (100 if char == "百" else 10)
            number = 0
    return total + number


def read_detail(bundle, index, receipt):
    if not 0 <= index < len(bundle.facts):
        raise ValueError("fact reference out of range")
    fact = bundle.facts[index]
    refs = {r.id: r for r in bundle.evidence_refs}
    sources = {s.id: (i, s) for i, s in enumerate(bundle.sources, 1)}
    rows = [f"[F{index + 1}] {fact_text(fact, receipt)}"]
    if external_preference(fact.assertion):
        return "\n".join(rows)
    if fact.kind == "effect" or has_effect_claim(fact.assertion):
        return "\n".join(rows) + "\n资料文字不替代 A/C 回执，不用原稿推断完成。"
    if (
        fact.detail
        and not has_effect_claim(fact.detail)
        and not external_preference(fact.detail)
    ):
        rows.append(f"详情：{fact.detail}")
    for identity in fact.evidence_refs:
        ref = refs[identity]
        number, source = sources[ref.source_id]
        original = ref.excerpt or source.text
        if has_effect_claim(original) or external_preference(original):
            rows.append(read_source(bundle, number - 1, receipt))
        else:
            rows.append(
                f"[S{number}] {source.title}\n原文：{original or '此来源未提供原文。'}"
            )
    return "\n".join(rows)


def read_source(bundle, index, receipt):
    source = bundle.sources[index]
    heading = f"[S{index + 1}] {source.title}"
    if external_preference(source.text):
        return (
            heading + "\n来源中的用户偏好说法尚未经本会话显式确认，不作为会话偏好引用。"
        )
    if has_effect_claim(source.text):
        linked_refs = {r.id for r in bundle.evidence_refs if r.source_id == source.id}
        effects = [
            f
            for f in bundle.facts
            if linked_refs.intersection(f.evidence_refs)
            and (f.kind == "effect" or has_effect_claim(f.assertion))
        ]
        return (
            heading
            + "\n来源文字不证明操作完成。\n"
            + (
                "\n".join(fact_text(f, receipt) for f in effects)
                or "效果未知，尚无可核对的 ACK 或只读确认。"
            )
        )
    return heading + f"\n原文：{source.text or '未提供原文。'}"
