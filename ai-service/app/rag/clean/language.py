"""Stage 5: Language Detection with Metadata Annotation Only (Non-destructive)."""

from __future__ import annotations

import re
from .config import LanguageDetectionConfig
from .models import BlockLanguageAnnotation, LanguageAnnotation, StageMetrics


class LanguageDetector:
    """Detects primary and block-level languages. NEVER deletes content."""

    _CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")
    _ASCII_ALPHA_PATTERN = re.compile(r"[a-zA-Z]")
    _JAPANESE_KANA_PATTERN = re.compile(r"[\u3040-\u30ff]")

    def __init__(self, config: LanguageDetectionConfig | None = None) -> None:
        self.config = config or LanguageDetectionConfig()

    def detect(self, text: str) -> tuple[LanguageAnnotation, StageMetrics]:
        if not self.config.enabled or not text.strip():
            annotation = LanguageAnnotation(
                primary_language="unknown",
                confidence=0.0,
                has_english_blocks=False,
                english_block_count=0,
                policy_action="retained_unfiltered",
                block_annotations=[],
            )
            metrics = StageMetrics(
                stage_index=5,
                stage_name="language_detection",
                before_chars=len(text),
                after_chars=len(text),
                removed_chars=0,
                removed_blocks=0,
                details={
                    "primary_language": "unknown",
                    "english_block_count": 0,
                    "policy": "retained_unfiltered",
                },
            )
            return annotation, metrics

        blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
        block_annotations: list[BlockLanguageAnnotation] = []
        english_blocks = 0

        for idx, block in enumerate(blocks):
            lang, conf = self._classify_text(block)
            is_eng = lang == "en"
            if is_eng:
                english_blocks += 1
            block_annotations.append(
                BlockLanguageAnnotation(
                    block_index=idx,
                    detected_language=lang,
                    confidence=conf,
                    char_count=len(block),
                    is_english=is_eng,
                )
            )

        overall_lang, overall_conf = self._classify_text(text)

        annotation = LanguageAnnotation(
            primary_language=overall_lang,
            confidence=overall_conf,
            has_english_blocks=english_blocks > 0,
            english_block_count=english_blocks,
            policy_action="retained_unfiltered",
            block_annotations=block_annotations,
        )

        metrics = StageMetrics(
            stage_index=5,
            stage_name="language_detection",
            before_chars=len(text),
            after_chars=len(text),
            removed_chars=0,
            removed_blocks=0,
            details={
                "primary_language": overall_lang,
                "confidence": overall_conf,
                "english_block_count": english_blocks,
                "policy": "retained_unfiltered",
                "pending_decision": "West review required for English block filtering policy",
            },
        )

        return annotation, metrics

    def _classify_text(self, text: str) -> tuple[str, float]:
        cjk_count = len(self._CJK_PATTERN.findall(text))
        alpha_count = len(self._ASCII_ALPHA_PATTERN.findall(text))
        kana_count = len(self._JAPANESE_KANA_PATTERN.findall(text))
        total_letters = cjk_count + alpha_count + kana_count

        if total_letters == 0:
            return "unknown", 0.0

        if kana_count > 0 and (kana_count / total_letters) > 0.15:
            return "ja", round(kana_count / total_letters, 4)

        cjk_ratio = cjk_count / total_letters
        alpha_ratio = alpha_count / total_letters

        if cjk_ratio >= 0.40:
            return "zh", round(cjk_ratio, 4)
        if alpha_ratio >= 0.75:
            return "en", round(alpha_ratio, 4)
        if cjk_ratio > 0.1 and alpha_ratio > 0.3:
            return "mixed", round(max(cjk_ratio, alpha_ratio), 4)

        return "unknown", 0.3
