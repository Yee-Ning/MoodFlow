"""Integrity rejection is distinct from an optional source outage."""

from app.agent.handoff.integrity import TransferIntegrityConflict


class SourceIntegrityError(TransferIntegrityConflict):
    """Sanitized hard rejection; the graph must not synthesize a fallback result."""
