"""Validated and versioned configuration-backed Clean Rule Registry."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Pattern

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import CleanPipelineConfig, CleanRuleConfig
from .corpus import NormalizedDocumentContext
from .errors import RuleRegistryError


class RuleAction(StrEnum):
    NORMALIZE = "NORMALIZE"
    REPLACE = "REPLACE"
    DROP_LINE = "DROP_LINE"
    DROP_BLOCK = "DROP_BLOCK"


class RuleScopeType(StrEnum):
    GLOBAL = "GLOBAL"
    SOURCE_SPECIFIC = "SOURCE_SPECIFIC"
    DOCUMENT_TYPE_SPECIFIC = "DOCUMENT_TYPE_SPECIFIC"


class RuleScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: RuleScopeType
    sources: list[str] = Field(default_factory=list)
    document_types: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scope(self) -> "RuleScope":
        if self.type is RuleScopeType.GLOBAL and (
            self.sources or self.document_types
        ):
            raise ValueError("GLOBAL scope cannot restrict source or document type")
        if self.type is RuleScopeType.SOURCE_SPECIFIC and not self.sources:
            raise ValueError("SOURCE_SPECIFIC scope requires sources")
        if (
            self.type is RuleScopeType.DOCUMENT_TYPE_SPECIFIC
            and not self.document_types
        ):
            raise ValueError(
                "DOCUMENT_TYPE_SPECIFIC scope requires document_types"
            )
        return self


class CleanRule(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, arbitrary_types_allowed=True
    )

    id: str
    name: str
    description: str
    scope: RuleScope
    action: RuleAction
    pattern: str
    replacement: str | None = None
    flags: list[str]
    priority: int
    enabled: bool
    safe: bool
    reason: str
    version: str
    compiled_pattern: Pattern[str] | None = Field(default=None, exclude=True)


class RuleRegistry:
    NORMALIZE_OPERATIONS = frozenset(
        {
            "UNICODE_NFC",
            "NEWLINES",
            "TRAILING_WHITESPACE",
            "BLANK_LINES",
            "FINAL_NEWLINE",
        }
    )
    FLAG_VALUES = {
        "IGNORECASE": re.IGNORECASE,
        "MULTILINE": re.MULTILINE,
        "DOTALL": re.DOTALL,
    }

    def __init__(self, config: CleanPipelineConfig) -> None:
        self.clean_version = config.clean_version
        try:
            self.rules = tuple(self._build_rule(rule) for rule in config.rules)
        except (ValueError, re.error) as exc:
            raise RuleRegistryError("rule_registry_invalid") from exc
        self.known_sources = frozenset(
            [*config.known_sources]
            + [
                source
                for rule in self.rules
                for source in rule.scope.sources
                if rule.enabled
            ]
        )
        self.known_document_types = frozenset(
            [*config.known_document_types]
            + [
                document_type
                for rule in self.rules
                for document_type in rule.scope.document_types
                if rule.enabled
            ]
        )

    def is_known_source(self, source: str) -> bool:
        return source in self.known_sources

    def is_known_document_type(self, document_type: str | None) -> bool:
        return bool(
            document_type and document_type in self.known_document_types
        )

    def rules_for(
        self, context: NormalizedDocumentContext
    ) -> tuple[CleanRule, ...]:
        global_rules = self._stage_rules(RuleScopeType.GLOBAL, context)
        if not self.is_known_source(
            context.source
        ) or not self.is_known_document_type(context.document_type):
            return global_rules
        return (
            *global_rules,
            *self._stage_rules(RuleScopeType.SOURCE_SPECIFIC, context),
            *self._stage_rules(
                RuleScopeType.DOCUMENT_TYPE_SPECIFIC, context
            ),
        )

    def _stage_rules(
        self,
        scope_type: RuleScopeType,
        context: NormalizedDocumentContext,
    ) -> tuple[CleanRule, ...]:
        rules = [
            rule
            for rule in self.rules
            if rule.enabled
            and rule.scope.type is scope_type
            and self._matches(rule.scope, context)
        ]
        return tuple(sorted(rules, key=lambda rule: (rule.priority, rule.id)))

    @staticmethod
    def _matches(
        scope: RuleScope, context: NormalizedDocumentContext
    ) -> bool:
        if scope.sources and context.source not in scope.sources:
            return False
        if scope.document_types and context.document_type not in scope.document_types:
            return False
        return True

    def _build_rule(self, config: CleanRuleConfig) -> CleanRule:
        action = RuleAction(config.action)
        scope = RuleScope.model_validate(config.scope)
        if scope.type is RuleScopeType.GLOBAL and not config.safe:
            raise ValueError("GLOBAL rules must be explicitly safe")
        if action is RuleAction.NORMALIZE:
            if config.pattern not in self.NORMALIZE_OPERATIONS:
                raise ValueError(
                    f"unsupported normalize operation:{config.pattern}"
                )
            compiled = None
        else:
            unknown_flags = set(config.flags) - self.FLAG_VALUES.keys()
            if unknown_flags:
                raise ValueError(f"unknown regex flags:{sorted(unknown_flags)}")
            flags = re.NOFLAG
            for name in config.flags:
                flags |= self.FLAG_VALUES[name]
            compiled = re.compile(config.pattern, flags)
        return CleanRule(
            **config.model_dump(exclude={"scope", "action"}),
            scope=scope,
            action=action,
            compiled_pattern=compiled,
        )
