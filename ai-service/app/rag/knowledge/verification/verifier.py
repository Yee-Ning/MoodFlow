"""Claim verification against only the current chunk and evidence span."""

from __future__ import annotations

import asyncio

from pydantic import ValidationError

from app.rag.enrichment.models import EnrichedChunk

from ..claims.models import Claim, ClaimPredicate, ClaimSource
from ..config import KnowledgeConfig
from ..structured import strict_model_schema
from .models import ClaimVerification, VerificationStatus
from .prompts import CLAIM_VERIFICATION_PROMPT_V1
from .runtime import ClaimVerificationRuntime, ExistingLLMClaimVerificationRuntime


_PREDICATE_CUES: dict[ClaimPredicate, tuple[str, ...]] = {
    ClaimPredicate.BORN_ON: ("出生", "生于", "生於"),
    ClaimPredicate.DEBUTED: ("出道", "进入乐坛", "進入樂壇"),
    ClaimPredicate.RELEASED: ("发行", "發行", "发布", "發布", "推出"),
    ClaimPredicate.FIRST_ALBUM_OF: ("首张专辑", "首張專輯", "首张唱片", "首張唱片"),
    ClaimPredicate.INCLUDES_TRACK: ("收录", "收錄", "包含"),
    ClaimPredicate.HAS_GENRE: ("风格", "風格", "曲风", "曲風", "属于", "屬於"),
    ClaimPredicate.CONTAINS_MUSIC_ELEMENT: ("融合", "融入", "结合", "結合"),
    ClaimPredicate.WON_AWARD: ("获奖", "獲獎", "获得", "獲得", "荣获", "榮獲"),
    ClaimPredicate.CHART_POSITION: ("榜", "排名", "排行"),
    ClaimPredicate.PERFORMED_AT: ("演唱会", "演唱會", "演出"),
    ClaimPredicate.COLLABORATED_WITH: ("合作", "合唱", "共同创作", "共同創作"),
    ClaimPredicate.SIGNED_TO: ("签约", "簽約", "加盟"),
    ClaimPredicate.WROTE_LYRICS_FOR: ("作词", "作詞", "填词", "填詞"),
    ClaimPredicate.COMPOSED_FOR: ("作曲",),
    ClaimPredicate.ARRANGED: ("编曲", "編曲"),
    ClaimPredicate.PRODUCED: ("制作", "製作", "制作人", "製作人"),
}

_NEGATION_CUES: dict[ClaimPredicate, tuple[str, ...]] = {
    ClaimPredicate.DEBUTED: ("未出道", "没有出道", "沒有出道"),
    ClaimPredicate.RELEASED: (
        "未发行",
        "未發行",
        "没有发行",
        "沒有發行",
        "并未推出",
        "並未推出",
    ),
    ClaimPredicate.INCLUDES_TRACK: ("未收录", "未收錄", "不包含", "不收录", "不收錄"),
}


class ClaimVerifier:
    def __init__(
        self,
        config: KnowledgeConfig,
        *,
        runtime: ClaimVerificationRuntime | None = None,
    ) -> None:
        self.config = config
        self.runtime = runtime or ExistingLLMClaimVerificationRuntime()

    async def verify(self, claim: Claim, chunk: EnrichedChunk) -> ClaimVerification:
        if claim.source_chunk_id != chunk.chunk_id:
            return self._insufficient("claim_source_chunk_mismatch")
        if claim.evidence_span not in chunk.content:
            return self._insufficient("claim_evidence_not_in_current_chunk")
        if not self._time_is_grounded(claim):
            return self._insufficient("claim_time_not_grounded")
        if self._is_contradicted(claim):
            return ClaimVerification(
                status=VerificationStatus.CONTRADICTED,
                confidence=0.99,
                evidence_span=claim.evidence_span,
                reason="source evidence explicitly negates the claimed predicate",
            )
        if claim.extraction_source == ClaimSource.RULE:
            return self._verify_rule_claim(claim, chunk)
        if not self.config.llm_enabled:
            return self._insufficient("verification_requires_structured_model")
        try:
            return await self._generate(claim, chunk)
        except Exception as exc:
            return self._insufficient(
                f"verification_unavailable:{self._error_code(exc)}"
            )

    @staticmethod
    def _is_contradicted(claim: Claim) -> bool:
        return any(
            cue in claim.evidence_span
            for cue in _NEGATION_CUES.get(claim.predicate, ())
        )

    @staticmethod
    def _time_is_grounded(claim: Claim) -> bool:
        if claim.time is None:
            return True
        time = claim.time
        candidates = [
            time.raw,
            str(time.year) if time.year is not None else None,
            str(time.date.year) if time.date is not None else None,
            str(time.start_year) if time.start_year is not None else None,
            str(time.end_year) if time.end_year is not None else None,
        ]
        return any(
            candidate is not None and candidate in claim.evidence_span
            for candidate in candidates
        )

    def _verify_rule_claim(
        self, claim: Claim, chunk: EnrichedChunk
    ) -> ClaimVerification:
        cues = _PREDICATE_CUES.get(claim.predicate, ())
        if (
            cues
            and not any(cue in claim.evidence_span for cue in cues)
            and not self._table_supports_predicate(claim, chunk, cues)
        ):
            return self._insufficient("rule_predicate_cue_missing")
        if claim.time is not None:
            raw = claim.time.raw
            year = str(claim.time.year) if claim.time.year is not None else None
            if (
                raw
                and raw not in claim.evidence_span
                and (year is None or year not in claim.evidence_span)
            ):
                return self._insufficient("rule_time_not_grounded")
        return ClaimVerification(
            status=VerificationStatus.SUPPORTED,
            confidence=min(0.99, claim.confidence),
            evidence_span=claim.evidence_span,
            reason="deterministic predicate and temporal cues are present in the source evidence",
        )

    @staticmethod
    def _table_supports_predicate(
        claim: Claim,
        chunk: EnrichedChunk,
        cues: tuple[str, ...],
    ) -> bool:
        table = chunk.table_data
        if table is None or not any(
            cue in header for cue in cues for header in table.headers
        ):
            return False
        rows = {
            f"| {' | '.join(str(cell).strip() for cell in row)} |" for row in table.rows
        }
        return claim.evidence_span in rows

    async def _generate(self, claim: Claim, chunk: EnrichedChunk) -> ClaimVerification:
        payload: dict[str, object] = {
            "claim": claim.model_dump(mode="json"),
            "chunk_content": chunk.content,
            "evidence_span": claim.evidence_span,
        }
        last_error: Exception | None = None
        for attempt in range(self.config.retry_count + 1):
            instructions = CLAIM_VERIFICATION_PROMPT_V1
            if attempt:
                instructions += "\nCorrect the previous invalid or ungrounded response."
            try:
                raw = await asyncio.wait_for(
                    self.runtime.generate(
                        payload,
                        instructions=instructions,
                        schema=strict_model_schema(ClaimVerification),
                        schema_name=f"music_claim_verification_{self.config.prompt_version}",
                    ),
                    timeout=self.config.timeout_seconds,
                )
                output = ClaimVerification.model_validate_json(raw)
                if (
                    output.evidence_span is not None
                    and output.evidence_span not in chunk.content
                ):
                    raise ValueError("verification_evidence_not_in_current_chunk")
                return output
            except Exception as exc:
                last_error = exc
        raise RuntimeError(self._error_code(last_error)) from last_error

    @staticmethod
    def _insufficient(reason: str) -> ClaimVerification:
        return ClaimVerification(
            status=VerificationStatus.INSUFFICIENT_EVIDENCE,
            confidence=1.0,
            evidence_span=None,
            reason=reason,
        )

    @staticmethod
    def _error_code(error: Exception | None) -> str:
        if isinstance(error, asyncio.TimeoutError):
            return "claim_verification_timeout"
        if isinstance(error, ValidationError):
            return "claim_verification_invalid_output"
        if isinstance(error, (ValueError, RuntimeError)):
            return str(error)[:500]
        return "claim_verification_request_failed"
