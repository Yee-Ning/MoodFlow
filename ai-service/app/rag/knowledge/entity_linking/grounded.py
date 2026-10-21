"""Opt-in v6 local entity linking; document context never counts as a body mention.

Inject GroundedEntityLinker into KnowledgePipeline explicitly. The existing v5
linker and production defaults are intentionally unchanged. Detailed decisions
are persisted by the offline repair track as a hash-bound sidecar.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from app.rag.enrichment.models import EnrichedChunk
from app.rag.enrichment.normalizer import normalize_text

from ..config import KnowledgeConfig
from .linker import EntityLinker
from .models import EntityLinkingResult, EntityType, UnresolvedMention
from .registry import EntityRegistry, normalize_alias

if TYPE_CHECKING:
    from .b1_scope import B1Scope

POLICY_VERSION = "body-grounding-v6"
_MUSIC_WORK_TYPES = {EntityType.SONG, EntityType.ALBUM}
_GENRE_END = re.compile(
    r"^(?:$|[\s、,，。.!！?？:：;；/()（）《》\[\]“”\"'\-]|"
    r"音乐|音樂|元素|风格|風格|曲风|曲風|文化|节奏|節奏|流派|歌曲|歌|乐|樂|式|"
    r"民谣|民謠|摇滚|搖滾|电子|電子|饶舌|饒舌|说唱|說唱|流行)"
)


def boilerplate_reason(content: str, section_path: list[str]) -> str | None:
    """Reject clear UI/empty fragments; short genuine lists remain admissible."""
    text = content.strip()
    stripped = re.sub(r"\[(?:编辑|編輯)\]|[\s|#>*`\-]", "", text)
    if not stripped:
        return "empty_or_edit_only"
    if re.fullmatch(r"[\d.,:;/_ ]+", stripped):
        return "numeric_metadata_fragment"
    if section_path and section_path[-1].casefold() in {
        "thesis type",
        "graduation year",
        "degree",
        "publication date",
    }:
        return "catalog_metadata_field"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if all(re.fullmatch(r"(?:\d+[.)]\s*)?Track\s+\d+", line, re.I) for line in lines):
        return "placeholder_track_list"
    if re.search(r"查[论論]編|查[论論]编", text):
        return "navigation_template"
    if any(s in {"外部連結", "外部链接"} for s in section_path) and re.match(
        r"\|\s*[獲获][獎奖]及[榮荣][譽誉]\s*\|", text
    ):
        return "award_navigation_template"
    if (
        sum(cue in text for cue in ("首页", "分类索引", "创建账号", "维基社群", "登录"))
        >= 3
    ):
        return "site_navigation"
    if len(text) < 180 and (
        re.search(r"跳到主要|跳转到内容|维基百科，自由的百科全书", text)
        or ("Apple Music" in text and "Spotify" in text)
    ):
        return "site_controls"
    folded = text.casefold()
    if (
        len(text) < 400
        and lines
        and lines[0].casefold() == "advertisement"
        and re.search(r"lifestyle|entertainment|other asian pop", folded)
    ):
        return "advertisement_topic_navigation"
    if (
        "隐藏分类" in text
        or "隱藏分類" in text
        or "本页面最后修订" in text
        or "本頁面最後修訂" in text
    ):
        return "wiki_category_and_footer"
    if (
        sum(
            cue in text
            for cue in (
                "法律條款",
                "隱私權政策",
                "供應商行為準則",
                "集團徵才",
                "關於我們",
                "法律条款",
                "隐私权政策",
                "供应商行为准则",
                "集团征才",
            )
        )
        >= 3
    ):
        return "legal_and_recruiting_footer"
    cells = [cell.strip() for cell in text.splitlines() if cell.strip("| ")]
    if (
        text.startswith("|")
        and len(text) < 300
        and len(cells) <= 2
        and re.search(r"https?://", text)
        and re.search(r"\d{4}年.*\d{1,2}:\d{2}", text)
    ):
        return "article_header_without_body"
    if (
        sum(
            cue in folded
            for cue in (
                "copyright",
                "all rights reserved",
                "privacy policy",
                "版权所有",
                "广告服务",
                "terms of service",
                "manage my subscription",
            )
        )
        >= 2
    ):
        return "footer_or_subscription"
    if (
        sum(cue in text for cue in ("分类信息", "招商", "年赚", "利润", "隆胸", "疱疹"))
        >= 3
    ):
        return "commercial_advertising"
    return None


def film_document(content: str) -> bool:
    """Use fetched text, not catalog labels, to recognize a film infobox."""
    return (
        sum(
            bool(re.search(pattern, content, re.I))
            for pattern in (
                r"\|\s*(?:导演|導演)\s*\|",
                r"\|\s*(?:主演|演员|演員)\s*\|",
                r"\|\s*(?:片长|片長)\s*\|",
                r"上映日期",
                r"\|\s*(?:编剧|編劇)\s*\|",
            )
        )
        >= 3
    )


def body_occurrences(content: str, aliases: tuple[str, ...]) -> list[dict]:
    """NFKC/casefold matching with original, exact Python-character offsets."""
    normalized: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for index, char in enumerate(content):
        value = " " if char.isspace() else normalize_text(char).casefold()
        for item in value:
            if item == " " and normalized and normalized[-1] == " ":
                ends[-1] = index + 1
                continue
            normalized.append(item)
            starts.append(index)
            ends.append(index + 1)
    text = "".join(normalized)
    occupied: list[tuple[int, int]] = []
    result = []
    for alias in sorted(
        aliases, key=lambda a: (-len(normalize_alias(a)), normalize_alias(a), a)
    ):
        key = normalize_alias(alias)
        if not key:
            continue
        pattern = re.escape(key)
        if key.isascii() and key[0].isalnum() and key[-1].isalnum():
            # A hyphenated person such as Jay-Z is not the short name Jay.
            pattern = rf"(?<![a-z0-9_\-]){pattern}(?![a-z0-9_\-])"
        for match in re.finditer(pattern, text):
            start, end = starts[match.start()], ends[match.end() - 1]
            if any(start < right and left < end for left, right in occupied):
                continue
            quote = content[start:end]
            if normalize_alias(quote) != key:
                continue
            occupied.append((start, end))
            result.append({"start": start, "end": end, "quote": quote, "alias": alias})
    return sorted(result, key=lambda item: (item["start"], item["end"]))


@dataclass(frozen=True)
class GroundedLinkAudit:
    chunk_id: str
    body_sha256: str
    result: EntityLinkingResult
    body_matches: tuple[dict, ...]
    rejected: tuple[dict, ...]
    context_only_mentions: tuple[dict, ...]
    excluded_reason: str | None
    policy_version: str = POLICY_VERSION
    b1_decisions: tuple[dict, ...] = ()
    scope_sha256: str | None = None

    def payload(self) -> dict:
        payload = {
            "policy_version": self.policy_version,
            "chunk_id": self.chunk_id,
            "body_sha256": self.body_sha256,
            "body_matches": list(self.body_matches),
            "rejected": list(self.rejected),
            "context_only_mentions": list(self.context_only_mentions),
            "excluded_reason": self.excluded_reason,
        }
        if self.scope_sha256 is not None:
            payload["scope_sha256"] = self.scope_sha256
            payload["b1_decisions"] = list(self.b1_decisions)
        return payload


class GroundedEntityLinker:
    """Rule-only implementation of the existing linker interface, opt-in v6."""

    def __init__(
        self,
        config: KnowledgeConfig,
        *,
        registry: EntityRegistry | None = None,
        film_documents: frozenset[str] = frozenset(),
        b1_scope: B1Scope | None = None,
    ):
        if config.entity_linking_version not in {"v6", "v7"} or config.llm_enabled:
            raise ValueError("grounded_linker_requires_v6_rule_only")
        if config.entity_linking_version == "v7" and b1_scope is None:
            raise ValueError("v7_requires_explicit_b1_scope")
        if config.entity_linking_version == "v6" and b1_scope is not None:
            raise ValueError("v6_cannot_enable_b1_retry")
        self.config = config
        self.registry = registry or EntityRegistry.load()
        self.delegate = EntityLinker(config, registry=self.registry)
        self.film_documents = film_documents
        self.b1_scope = b1_scope

    async def link(self, chunk: EnrichedChunk) -> EntityLinkingResult:
        return self.link_with_evidence(chunk).result

    def link_with_evidence(
        self, chunk: EnrichedChunk, *, document_is_film: bool = False
    ) -> GroundedLinkAudit:
        base = self._link_v6_with_evidence(chunk, document_is_film=document_is_film)
        if self.config.entity_linking_version == "v7":
            return self._retry_b1(chunk, base)
        return base

    def _retry_b1(
        self, chunk: EnrichedChunk, base: GroundedLinkAudit
    ) -> GroundedLinkAudit:
        # Import only in the explicitly selected v7 branch. v5/v6 are unchanged.
        from .b1_rules import infer_occurrence
        from .b1_scope import B1Occurrence

        linked = {e.entity_id: e for e in base.result.linked_entities}
        original_ids = frozenset(linked)
        pending = {m.normalized_text: m for m in base.result.unresolved_mentions}
        accepted = list(base.body_matches)
        rejected = list(base.rejected)
        decisions = []
        for entry in self.b1_scope.for_chunk(chunk.chunk_id):
            mention = pending.get(entry.normalized_text)
            found = [
                r
                for r in base.rejected
                if normalize_alias(r["quote"]) == entry.normalized_text
            ]
            actual = [
                B1Occurrence.model_validate(
                    {k: r[k] for k in B1Occurrence.model_fields}
                )
                for r in found
            ]
            if (
                entry.doc_id != chunk.doc_id
                or entry.body_sha256 != base.body_sha256
                or mention is None
                or tuple(mention.candidate_entity_ids) != entry.candidate_entity_ids
                or mention.reason != entry.reason
                or tuple(actual) != entry.occurrences
                or any(chunk.content[o.start : o.end] != o.quote for o in actual)
            ):
                raise ValueError(f"b1_scope_binding_mismatch:{entry.audit_id}")
            inferred = [
                infer_occurrence(chunk.content, o.start, o.end, self.registry)
                for o in actual
            ]
            targets = {d.entity_id for d in inferred if d is not None}
            record = {
                "audit_id": entry.audit_id,
                "normalized_text": entry.normalized_text,
                "before_reason": mention.reason,
                "status": "kept_unresolved",
            }
            if (
                all(d is not None for d in inferred)
                and len(targets) == 1
                and targets.issubset(entry.candidate_entity_ids)
                and not targets.intersection(original_ids)
            ):
                entity_id = next(iter(targets))
                entity = self.registry.get(entity_id)
                linked[entity_id] = self.registry.canonical(entity, 0.85)
                del pending[entry.normalized_text]
                positions = {(o.start, o.end) for o in actual}
                rejected = [
                    r for r in rejected if (r["start"], r["end"]) not in positions
                ]
                proofs = []
                for occurrence, decision in zip(found, inferred):
                    proof = {
                        "start": occurrence["start"],
                        "end": occurrence["end"],
                        "quote": occurrence["quote"],
                        "alias": occurrence["alias"],
                        "entity_id": entity_id,
                        "source": "body",
                        "resolution_reason": decision.rule_id,
                        "context_type": entity.entity_type.value,
                        "evidence_start": decision.evidence_start,
                        "evidence_end": decision.evidence_end,
                        "evidence_quote": decision.evidence_quote,
                    }
                    accepted.append(proof)
                    proofs.append(proof)
                record.update(status="resolved_b1", entity_id=entity_id, proofs=proofs)
            else:
                record["occurrence_decisions"] = [
                    {
                        "start": o.start,
                        "end": o.end,
                        "entity_id": d.entity_id if d else None,
                        "rule_id": d.rule_id if d else None,
                    }
                    for o, d in zip(actual, inferred)
                ]
            decisions.append(record)
        result = EntityLinkingResult(
            **{
                **base.result.model_dump(),
                "linked_entities": sorted(linked.values(), key=lambda e: e.entity_id),
                "unresolved_mentions": sorted(
                    pending.values(), key=lambda m: m.normalized_text
                ),
            }
        )
        return replace(
            base,
            result=result,
            body_matches=tuple(accepted),
            rejected=tuple(rejected),
            policy_version="body-grounding-v7-b1",
            b1_decisions=tuple(decisions),
            scope_sha256=self.b1_scope.sha256,
        )

    def _link_v6_with_evidence(
        self, chunk: EnrichedChunk, *, document_is_film: bool = False
    ) -> GroundedLinkAudit:
        document_is_film = document_is_film or chunk.doc_id in self.film_documents
        excluded = boilerplate_reason(chunk.content, chunk.section_path)
        body_hash = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
        context = tuple(
            {"surface": m.text, "source": m.source, "scope": "document_context"}
            for m in self.delegate._collect_mentions(chunk)
            if normalize_alias(m.text) not in normalize_alias(chunk.content)
        )
        if excluded:
            return GroundedLinkAudit(
                chunk.chunk_id,
                body_hash,
                EntityLinkingResult(),
                (),
                (),
                context,
                excluded,
            )
        linked = {}
        unresolved = {}
        accepted, rejected = [], []
        for occurrence in body_occurrences(
            chunk.content, self.registry.detectable_aliases()
        ):
            surface = occurrence["quote"]
            candidates = self.registry.candidates(surface)
            start, end = occurrence["start"], occurrence["end"]
            before = chunk.content[max(0, start - 24) : start]
            after = chunk.content[end : end + 24]
            direct_type = None
            film_cue = bool(
                re.search(r"(?:电影|電影|film|movie)\s*[《“\"\[]?\s*$", before, re.I)
            )
            if re.search(r"(?:专辑|專輯|唱片|album)\s*[《“\"\[]?\s*$", before, re.I):
                direct_type = EntityType.ALBUM
            elif re.search(
                r"(?:歌曲|单曲|單曲|song|track)\s*[《“\"\[]?\s*$", before, re.I
            ):
                direct_type = EntityType.SONG
            context_type = (
                self.delegate._infer_jay_context_type(chunk.content)
                if normalize_alias(surface) == "jay"
                else direct_type
                or self.delegate._infer_context_type(chunk.content, surface)
            )
            # A coordinated list of Chinese-style songs is a work context, not
            # the entity type GENRE merely because the predicate names a style.
            if (
                direct_type is None
                and start > 0
                and end < len(chunk.content)
                and chunk.content[start - 1] in "《〈"
                and chunk.content[end] in "》〉"
                and any(c.entity_type == EntityType.SONG for c in candidates)
            ):
                sentence_start = (
                    max(chunk.content.rfind(mark, 0, start) for mark in "。！？\n") + 1
                )
                sentence_end = min(
                    [
                        p
                        for mark in "。！？\n"
                        if (p := chunk.content.find(mark, end)) >= 0
                    ]
                    or [len(chunk.content)]
                )
                sentence = chunk.content[sentence_start:sentence_end]
                if re.search(r"歌曲|曲目|歌词|歌詞|中国风作品|中國風作品", sentence):
                    context_type = EntityType.SONG
            reason = None
            if any(c.entity_type in _MUSIC_WORK_TYPES for c in candidates) and (
                film_cue
                or (
                    (document_is_film or film_document(chunk.content))
                    and direct_type is None
                )
            ):
                reason = "film_music_work_type_conflict"
            elif candidates and all(
                c.entity_type == EntityType.GENRE for c in candidates
            ):
                if not surface.isascii() and not _GENRE_END.search(after):
                    reason = "genre_is_substring_of_larger_surface"
                enclosing = next(
                    (
                        m.group(0)
                        for m in re.finditer(r"《[^》]+》", chunk.content)
                        if m.start() < start and end < m.end()
                    ),
                    None,
                )
                if enclosing:
                    reason = "genre_inside_quoted_work_title"
            if (
                direct_type is not None
                and candidates
                and not any(c.entity_type == direct_type for c in candidates)
            ):
                reason = reason or "explicit_work_type_conflict"
            resolution = self.registry.resolve(surface, context_type=context_type)
            if reason is None and resolution.entity is None:
                reason = resolution.reason
            if reason is not None:
                ids = sorted(c.entity_id for c in candidates)
                rejected.append(
                    {**occurrence, "reason": reason, "candidate_entity_ids": ids}
                )
                unresolved.setdefault(
                    normalize_alias(surface),
                    UnresolvedMention(
                        text=surface,
                        normalized_text=normalize_alias(surface),
                        type_hint="unknown",
                        confidence=0.85,
                        candidate_entity_ids=ids,
                        reason=reason,
                    ),
                )
                continue
            entity = resolution.entity
            canonical = self.registry.canonical(
                entity, min(0.85, resolution.confidence)
            )
            linked[entity.entity_id] = canonical
            accepted.append(
                {
                    **occurrence,
                    "entity_id": entity.entity_id,
                    "source": "body",
                    "resolution_reason": resolution.reason,
                    "context_type": context_type.value if context_type else None,
                }
            )
        if len(linked) > 200 or len(unresolved) > 200:
            raise ValueError("grounded_linker_contract_capacity_exceeded")
        result = EntityLinkingResult(
            linked_entities=sorted(linked.values(), key=lambda e: e.entity_id),
            unresolved_mentions=sorted(
                unresolved.values(), key=lambda m: m.normalized_text
            ),
        )
        return GroundedLinkAudit(
            chunk.chunk_id,
            body_hash,
            result,
            tuple(accepted),
            tuple(rejected),
            context,
            None,
        )
