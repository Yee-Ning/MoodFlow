"""Registry-backed entity linking public API."""

from .linker import EntityLinker
from .models import (
    CanonicalEntity,
    EntityLinkingResult,
    EntityLinkingStatus,
    EntityType,
    UnresolvedMention,
)
from .registry import EntityRegistry

__all__ = [
    "CanonicalEntity",
    "EntityLinker",
    "EntityLinkingResult",
    "EntityLinkingStatus",
    "EntityRegistry",
    "EntityType",
    "UnresolvedMention",
]
